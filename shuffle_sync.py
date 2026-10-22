#!/usr/bin/env python3
"""Sincroniza um iPod Shuffle 4a geracao sem iTunes (motor do MiTunes).

Qualquer MP3 (ou M4A) colocado na pasta "music" na raiz do iPod passa a tocar:
este script gera o banco que o firmware le (iPod_Control/iTunes/iTunesSD) apontando
direto para os arquivos dessa pasta, e cria as vozes do VoiceOver (nome da musica e
artista) com o espeak em portugues.

- Cada subpasta de "music" vira uma playlist (com o nome falado pelo VoiceOver).
- Arquivos sao tocados na ordem alfabetica do caminho.

Formato do iTunesSD baseado no projeto nims11/IPod-Shuffle-4g (GPL-2.0):
https://github.com/nims11/IPod-Shuffle-4g . Este arquivo tambem e GPL-2.0.

Uso: python3 shuffle_sync.py /media/jorge/EDIPOD
"""
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

EXTENSOES = {".mp3": 1, ".m4a": 2, ".m4b": 2}  # tipo de arquivo no iTunesSD
MARCA = "iPod_Control/iTunes/.assinatura_music"  # o que ja foi sincronizado
CONFIG = Path.home() / ".config" / "mitunes.json"  # preferencias do MiTunes (ex.: voz ligada)


def carregar_config():
    try:
        return json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        return {"voz": True}


def salvar_config(cfg):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=1))
VOZ = ["espeak", "-v", "pt-br", "-s", "150"]


# ---------- Tags ID3 (titulo/artista) sem dependencias ----------

def _texto_id3(dados):
    codificacao, corpo = dados[0], dados[1:]
    codec = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}.get(codificacao, "latin-1")
    return corpo.decode(codec, "ignore").strip("\x00").strip()


def ler_tags(arquivo):
    """Retorna (titulo, artista) das tags ID3v2 de um MP3, ou (None, None)."""
    try:
        with open(arquivo, "rb") as f:
            cab = f.read(10)
            if cab[:3] != b"ID3":
                return None, None
            versao = cab[3]
            tamanho = (cab[6] << 21) | (cab[7] << 14) | (cab[8] << 7) | cab[9]
            bloco = f.read(tamanho)
    except OSError:
        return None, None
    tags, i = {}, 0
    ids = {"TIT2": "titulo", "TPE1": "artista", "TT2": "titulo", "TP1": "artista"}
    while i < len(bloco) - 10:
        if versao == 2:
            fid, tam, i0 = bloco[i:i + 3].decode("latin-1", "ignore"), int.from_bytes(bloco[i + 3:i + 6], "big"), i + 6
        else:
            fid = bloco[i:i + 4].decode("latin-1", "ignore")
            raw = bloco[i + 4:i + 8]
            tam = ((raw[0] << 21) | (raw[1] << 14) | (raw[2] << 7) | raw[3]) if versao == 4 else int.from_bytes(raw, "big")
            i0 = i + 10
        if not fid.strip("\x00") or tam <= 0:
            break
        if fid in ids:
            tags[ids[fid]] = _texto_id3(bloco[i0:i0 + tam])
        i = i0 + tam
    return tags.get("titulo") or None, tags.get("artista") or None


# ---------- VoiceOver ----------

def dbid_de(texto):
    return hashlib.md5(texto.encode("utf-8")).digest()[:8]


def falar(texto, destino):
    """Gera o WAV do VoiceOver (espeak ja produz mono 16 bits 22050 Hz, igual ao do iPod)."""
    if destino.exists():
        return
    try:
        subprocess.run(VOZ + ["-w", str(destino), texto], check=True, capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass  # sem voz para esta musica; o iPod continua tocando


# ---------- iTunesSD ----------

def bloco_musica(caminho_ipod, tipo, dbid):
    nome = caminho_ipod.encode("utf-8")[:255]
    return struct.pack(
        "<4sIIIII256sIBBBBIIIIIIIHHQ8sI32s",
        b"rths", 0x174, 0, 0, 0, tipo, nome,
        0, 1, 0, 0, 0,          # bookmark, dontskip, remember, unintalbum, unknown
        0x200, 0x200, 0, 0, 0, 0, 0,  # pregap, postgap, numsamples, ?, gapless, ?, albumid
        1, 0, 0, dbid, 0, b"\x00" * 32,
    )


def bloco_playlist(indices, dbid, tipo):
    cab = struct.pack("<4sIII8sI16s", b"lphs", 44 + 4 * len(indices), len(indices), len(indices), dbid, tipo, b"\x00" * 16)
    return cab + b"".join(struct.pack("<I", i) for i in indices)


def montar_itunessd(musicas, playlists, voiceover):
    """musicas: [(caminho_ipod, tipo, dbid)]; playlists: [(dbid, [indices])]."""
    base_musicas = 64
    cab_musicas_len = 20 + 4 * len(musicas)
    blocos = [bloco_musica(*m) for m in musicas]
    offsets, pos = [], base_musicas + cab_musicas_len
    for b in blocos:
        offsets.append(pos)
        pos += len(b)
    secao_musicas = struct.pack("<4sIIQ", b"hths", cab_musicas_len, len(musicas), 0)
    secao_musicas += b"".join(struct.pack("<I", o) for o in offsets) + b"".join(blocos)

    # Playlist mestre ("Todas as musicas", voz embutida do iPod) + uma por subpasta
    listas = [bloco_playlist(list(range(len(musicas))), b"\x00" * 8, 1)]
    listas += [bloco_playlist(idx, dbid, 2) for dbid, idx in playlists if idx]
    base_listas = base_musicas + len(secao_musicas)
    cab_listas_len = 0x14 + 4 * len(listas)
    secao_listas = struct.pack("<4sII2s2s2s2s", b"hphs", cab_listas_len, len(listas), b"\xff\xff", b"\x01\x00", b"\xff\xff", b"\x00\x00")
    pos = base_listas + cab_listas_len
    for lista in listas:
        secao_listas += struct.pack("<I", pos)
        pos += len(lista)
    secao_listas += b"".join(listas)

    cab = struct.pack("<4sIIIIQBBHIII20s", b"bdhs", 0x02000003, 64, len(musicas), len(listas), 0,
                      0, int(voiceover), 0, len(musicas), base_musicas, base_listas, b"\x00" * 20)
    return cab + secao_musicas + secao_listas


# ---------- Sincronizacao ----------

def pasta_music(raiz):
    for nome in os.listdir(raiz):
        if nome.lower() == "music" and (raiz / nome).is_dir():
            return raiz / nome
    pasta = raiz / "music"
    pasta.mkdir()
    return pasta


def assinatura(raiz):
    """Lista de musicas com tamanho e data: muda quando algo e copiado ou apagado."""
    raiz = Path(raiz)
    linhas = []
    for pasta in raiz.iterdir():
        if pasta.name.lower() == "music" and pasta.is_dir():
            for p in sorted(pasta.rglob("*")):
                if p.is_file() and p.suffix.lower() in EXTENSOES:
                    st = p.stat()
                    linhas.append(f"{p.relative_to(raiz)}|{st.st_size}|{int(st.st_mtime)}")
    return "\n".join(linhas)


def sincronizar(raiz, voz=True):
    raiz = Path(raiz)
    if not (raiz / "iPod_Control").is_dir():
        raise SystemExit(f"{raiz} nao parece ser um iPod (sem iPod_Control)")
    music = pasta_music(raiz)
    falas_musicas = raiz / "iPod_Control" / "Speakable" / "Tracks"
    falas_listas = raiz / "iPod_Control" / "Speakable" / "Playlists"
    for pasta in (falas_musicas, falas_listas):
        shutil.rmtree(pasta, ignore_errors=True)  # refeitas abaixo (evita vozes orfas)
        pasta.mkdir(parents=True, exist_ok=True)
    voiceover = voz and shutil.which(VOZ[0]) is not None

    arquivos = sorted(
        (p for p in music.rglob("*") if p.is_file() and p.suffix.lower() in EXTENSOES and not p.name.startswith(".")),
        key=lambda p: str(p.relative_to(music)).lower(),
    )
    musicas, por_pasta = [], {}
    for i, arq in enumerate(arquivos):
        titulo, artista = ler_tags(arq) if arq.suffix.lower() == ".mp3" else (None, None)
        fala = f"{titulo or arq.stem}" + (f", {artista}" if artista else "")
        dbid = dbid_de(str(arq.relative_to(raiz)))
        if voiceover:
            falar(fala, falas_musicas / (dbid[::-1].hex() + ".wav"))
        musicas.append(("/" + arq.relative_to(raiz).as_posix(), EXTENSOES[arq.suffix.lower()], dbid))
        if arq.parent != music:
            por_pasta.setdefault(arq.parent, []).append(i)

    playlists = []
    for pasta, indices in sorted(por_pasta.items(), key=lambda kv: str(kv[0]).lower()):
        dbid = dbid_de("lista:" + str(pasta.relative_to(raiz)))
        if voiceover:
            falar(pasta.name, falas_listas / (dbid[::-1].hex() + ".wav"))
        playlists.append((dbid, indices))

    destino = raiz / "iPod_Control" / "iTunes" / "iTunesSD"
    temporario = destino.with_suffix(".novo")
    temporario.write_bytes(montar_itunessd(musicas, playlists, voiceover))
    os.replace(temporario, destino)
    (raiz / MARCA).write_text(assinatura(raiz))
    os.sync()
    print(f"iPod atualizado: {len(musicas)} musicas, {len(playlists)} playlists (pastas), voz: {'sim' if voiceover else 'nao'}")
    return len(musicas)


if __name__ == "__main__":
    sincronizar(sys.argv[1] if len(sys.argv) > 1 else "/media/jorge/EDIPOD")
