#!/usr/bin/env python3
"""Vigia o iPod Shuffle: sempre que ele e conectado, ou que a pasta "music" muda,
atualiza o banco de musicas sozinho (shuffle_sync.py).

Espera a copia de arquivos terminar (pasta sem mudancas por alguns segundos) antes de
sincronizar. Uso: python3 vigia.py [/media/jorge/EDIPOD]
"""
import subprocess
import sys
import time
import traceback
from pathlib import Path

from shuffle_sync import MARCA, assinatura, carregar_config, sincronizar

IPOD = Path(sys.argv[1] if len(sys.argv) > 1 else "/media/jorge/EDIPOD")
INTERVALO = 3
ESTAVEL = 2  # leituras iguais seguidas antes de sincronizar (~6 s)


def log(*args):
    print(time.strftime("%H:%M:%S"), *args, flush=True)


def avisar(texto):
    try:
        subprocess.run(["notify-send", "-i", "mitunes", "MiTunes", texto], timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


def main():
    anterior, iguais = None, 0
    log("Vigiando", IPOD)
    while True:
        try:
            if (IPOD / "iPod_Control").is_dir():
                atual = assinatura(IPOD)
                iguais = iguais + 1 if atual == anterior else 0
                anterior = atual
                marca = IPOD / MARCA
                ja_feito = marca.read_text() if marca.exists() else None
                if iguais >= ESTAVEL and atual != ja_feito:
                    log("Mudancas na pasta music, atualizando o iPod...")
                    total = sincronizar(IPOD, voz=carregar_config().get("voz", True))
                    log("Pronto. Pode ejetar o iPod.")
                    avisar(f"iPod atualizado: {total} musicas. Pode ejetar.")
            else:
                anterior, iguais = None, 0
        except Exception:
            log("Erro ao sincronizar:\n" + traceback.format_exc())
            anterior, iguais = None, 0
        time.sleep(INTERVALO)


if __name__ == "__main__":
    main()
