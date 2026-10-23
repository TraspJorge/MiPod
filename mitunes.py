#!/usr/bin/env python3
"""MiTunes: gerenciador com janela (GTK 3) para o iPod Shuffle 4a geracao, no lugar do iTunes.

- Mostra as musicas da pasta "music" do iPod, separadas por playlist (subpastas)
- Adicionar musicas (botao ou arrastando arquivos para a janela), criar playlists, remover
- Liga/desliga a voz (VoiceOver), mostra o espaco livre, ejeta o iPod
- Avisa e remove o virus Activador.vbs, se estiver no iPod

Usa shuffle_sync.py para gerar o banco que o iPod le. Licenca GPL-2.0.
"""
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from urllib.parse import unquote, urlparse

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

import shuffle_sync  # noqa: E402

TODAS = "__todas__"
SUSPEITOS = ("Activador.vbs",)
INVALIDOS_FAT = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Tema preto: usa o tema escuro nativo do elementary com destaque roxo ("grape"), para botoes,
# dialogos e campos ficarem corretos, e so escurece as areas de conteudo ate o preto.
TEMA_ROXO = "io.elementary.stylesheet.grape"
CSS_PRETO = b"""
treeview.view, scrolledwindow, viewport, stack { background-color: #000000; }
treeview.view header button { background-color: #0a0a0a; }
list.sidebar { background-color: #0a0a0a; }
treeview.view:selected, treeview.view:selected:backdrop,
list.sidebar row:selected, list.sidebar row:selected:backdrop { background-color: #6a2fc4; color: #ffffff; }
list.sidebar row:selected label, list.sidebar row:selected image { color: #ffffff; }
button.mitunes-alerta image, button.mitunes-alerta:backdrop image { color: #ff6b6b; -gtk-icon-palette: warning #ff6b6b; }
"""


def aplicar_tema_preto():
    configuracoes = Gtk.Settings.get_default()
    if Path("/usr/share/themes", TEMA_ROXO).is_dir():
        configuracoes.set_property("gtk-theme-name", TEMA_ROXO)
    configuracoes.set_property("gtk-application-prefer-dark-theme", True)
    provedor = Gtk.CssProvider()
    provedor.load_from_data(CSS_PRETO)
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provedor,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def achar_ipod():
    """Primeiro volume montado com iPod_Control (prefere o rotulo EDIPOD)."""
    candidatos = []
    for base in (Path("/media") / os.environ.get("USER", ""), Path("/run/media") / os.environ.get("USER", "")):
        if base.is_dir():
            candidatos += [p for p in base.iterdir() if (p / "iPod_Control").is_dir()]
    candidatos.sort(key=lambda p: p.name != "EDIPOD")
    return candidatos[0] if candidatos else None


def tamanho_legivel(n):
    for unidade in ("B", "KB", "MB", "GB"):
        if n < 1024 or unidade == "GB":
            return f"{n:.0f} {unidade}" if unidade in ("B", "KB") else f"{n:.1f} {unidade}".replace(".", ",")
        n /= 1024


class Janela(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="MiTunes")
        self.set_icon_name("mitunes")
        self.set_default_size(900, 560)
        self.ipod = None
        self.cfg = shuffle_sync.carregar_config()
        self.ocupado = False

        cabecalho = Gtk.HeaderBar(show_close_button=True, title="MiTunes")
        self.set_titlebar(cabecalho)
        self.cabecalho = cabecalho

        self.bt_adicionar = Gtk.Button.new_from_icon_name("list-add-symbolic", Gtk.IconSize.BUTTON)
        self.bt_adicionar.set_tooltip_text("Adicionar músicas")
        self.bt_adicionar.connect("clicked", self.on_adicionar)
        cabecalho.pack_start(self.bt_adicionar)

        self.bt_playlist = Gtk.Button.new_from_icon_name("folder-new-symbolic", Gtk.IconSize.BUTTON)
        self.bt_playlist.set_tooltip_text("Nova playlist")
        self.bt_playlist.connect("clicked", self.on_nova_playlist)
        cabecalho.pack_start(self.bt_playlist)

        self.bt_remover = Gtk.Button.new_from_icon_name("user-trash-symbolic", Gtk.IconSize.BUTTON)
        self.bt_remover.set_tooltip_text("Remover músicas selecionadas (Delete)")
        self.bt_remover.connect("clicked", self.on_remover)
        cabecalho.pack_start(self.bt_remover)

        self.bt_ejetar = Gtk.Button.new_from_icon_name("media-eject-symbolic", Gtk.IconSize.BUTTON)
        self.bt_ejetar.set_tooltip_text("Ejetar o iPod")
        self.bt_ejetar.connect("clicked", self.on_ejetar)
        cabecalho.pack_end(self.bt_ejetar)

        # Aviso de virus: so um icone pequeno de alerta, que aparece quando ha virus no iPod
        self.bt_virus = Gtk.Button.new_from_icon_name("dialog-warning-symbolic", Gtk.IconSize.BUTTON)
        self.bt_virus.get_style_context().add_class("mitunes-alerta")
        self.bt_virus.connect("clicked", self.on_remover_virus)
        self.bt_virus.set_no_show_all(True)
        cabecalho.pack_end(self.bt_virus)

        self.sw_voz = Gtk.Switch(active=self.cfg.get("voz", True), valign=Gtk.Align.CENTER)
        self.sw_voz.connect("notify::active", self.on_voz)
        caixa_voz = Gtk.Box(spacing=6)
        caixa_voz.pack_start(Gtk.Label(label="Voz"), False, False, 0)
        caixa_voz.pack_start(self.sw_voz, False, False, 0)
        caixa_voz.set_tooltip_text("VoiceOver: o iPod fala o nome da música e da playlist")
        cabecalho.pack_end(caixa_voz)

        raiz = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(raiz)

        self.pilha = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        raiz.pack_start(self.pilha, True, True, 0)

        # Tela "conecte o iPod"
        vazio = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        icone = Gtk.Image.new_from_icon_name("multimedia-player", Gtk.IconSize.DIALOG)
        icone.set_pixel_size(128)
        vazio.pack_start(icone, False, False, 0)
        titulo = Gtk.Label()
        titulo.set_markup("<span size='x-large' weight='bold'>Conecte o iPod Shuffle</span>")
        vazio.pack_start(titulo, False, False, 0)
        vazio.pack_start(Gtk.Label(label="Assim que ele aparecer no computador, as músicas são mostradas aqui."), False, False, 0)
        self.pilha.add_named(vazio, "vazio")

        # Tela principal: playlists | musicas
        painel = Gtk.Paned(position=220, wide_handle=False)
        self.pilha.add_named(painel, "ipod")

        self.lista_playlists = Gtk.ListBox(selection_mode=Gtk.SelectionMode.BROWSE)
        self.lista_playlists.get_style_context().add_class("sidebar")
        self.lista_playlists.connect("row-selected", lambda *_: self.mostrar_musicas())
        rolagem_pl = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        rolagem_pl.add(self.lista_playlists)
        painel.pack1(rolagem_pl, False, False)

        # colunas: titulo, artista, playlist, tamanho, caminho
        self.modelo = Gtk.ListStore(str, str, str, str, str)
        self.tabela = Gtk.TreeView(model=self.modelo, rubber_banding=True)
        self.tabela.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        for i, (nome, expandir) in enumerate((("Título", True), ("Artista", True), ("Playlist", False), ("Tamanho", False))):
            celula = Gtk.CellRendererText(ellipsize=Pango.EllipsizeMode.END)
            if nome == "Tamanho":
                celula.set_property("xalign", 1.0)
            coluna = Gtk.TreeViewColumn(nome, celula, text=i)
            coluna.set_expand(expandir)
            coluna.set_resizable(True)
            coluna.set_sort_column_id(i)
            self.tabela.append_column(coluna)
        self.tabela.connect("key-press-event", self.on_tecla)
        rolagem = Gtk.ScrolledWindow()
        rolagem.add(self.tabela)

        direita = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        direita.pack_start(rolagem, True, True, 0)
        dica = Gtk.Label(label="Arraste arquivos MP3 para esta janela para adicioná-los à playlist selecionada")
        dica.get_style_context().add_class("dim-label")
        dica.set_margin_top(6)
        dica.set_margin_bottom(6)
        direita.pack_start(dica, False, False, 0)
        painel.pack2(direita, True, False)

        # Rodape: espaco usado + status
        rodape = Gtk.Box(spacing=12, margin=10)
        self.barra = Gtk.LevelBar(min_value=0, max_value=1, hexpand=True, valign=Gtk.Align.CENTER)
        for nivel in ("low", "high", "full"):  # sem cores de alerta: e so o espaco ocupado
            self.barra.remove_offset_value(nivel)
        self.espaco = Gtk.Label(xalign=0)
        self.status = Gtk.Label(xalign=1)
        self.spinner = Gtk.Spinner()
        rodape.pack_start(self.espaco, False, False, 0)
        rodape.pack_start(self.barra, True, True, 0)
        rodape.pack_end(self.spinner, False, False, 0)
        rodape.pack_end(self.status, False, False, 0)
        raiz.pack_end(rodape, False, False, 0)
        raiz.pack_end(Gtk.Separator(), False, False, 0)

        # Arrastar e soltar arquivos
        self.drag_dest_set(Gtk.DestDefaults.ALL, [Gtk.TargetEntry.new("text/uri-list", 0, 0)], Gdk.DragAction.COPY)
        self.connect("drag-data-received", self.on_soltar)

        self.show_all()
        self.recarregar()  # estado inicial: sem iPod, botoes desativados
        self.verificar_ipod()
        GLib.timeout_add_seconds(2, self.verificar_ipod)

    # ---------- Estado ----------

    def verificar_ipod(self):
        ipod = achar_ipod()
        if ipod != self.ipod:
            self.ipod = ipod
            self.recarregar()
        elif ipod and not self.ocupado:
            self.atualizar_espaco()
        return True

    def recarregar(self, manter=None):
        conectado = self.ipod is not None
        for bt in (self.bt_adicionar, self.bt_playlist, self.bt_remover, self.bt_ejetar):
            bt.set_sensitive(conectado)
        if not conectado:
            self.cabecalho.set_subtitle("Desconectado")
            self.pilha.set_visible_child_name("vazio")
            self.bt_virus.hide()
            self.espaco.set_text("")
            self.barra.set_value(0)
            self.status.set_text("")
            return
        self.cabecalho.set_subtitle(self.ipod.name)
        self.pilha.set_visible_child_name("ipod")
        self.music = shuffle_sync.pasta_music(self.ipod)
        self.carregar_playlists(manter)
        self.mostrar_musicas()
        self.atualizar_espaco()
        self.verificar_virus()

    def carregar_playlists(self, manter=None):
        selecionada = manter or self.playlist_atual()
        for filho in self.lista_playlists.get_children():
            self.lista_playlists.remove(filho)
        itens = [(TODAS, "Todas as músicas", "media-playlist-shuffle-symbolic")]
        itens += [(str(p), p.name, "folder-music-symbolic") for p in sorted(self.music.iterdir(), key=lambda p: p.name.lower()) if p.is_dir()]
        alvo = None
        for chave, nome, icone in itens:
            linha = Gtk.ListBoxRow()
            linha.chave = chave
            caixa = Gtk.Box(spacing=8, margin=8)
            caixa.pack_start(Gtk.Image.new_from_icon_name(icone, Gtk.IconSize.MENU), False, False, 0)
            caixa.pack_start(Gtk.Label(label=nome, xalign=0, ellipsize=Pango.EllipsizeMode.END), True, True, 0)
            linha.add(caixa)
            self.lista_playlists.add(linha)
            if chave == selecionada or alvo is None:
                alvo = linha if chave == selecionada or alvo is None else alvo
        self.lista_playlists.show_all()
        self.lista_playlists.select_row(alvo)

    def playlist_atual(self):
        linha = self.lista_playlists.get_selected_row()
        return linha.chave if linha else TODAS

    def pasta_destino(self):
        chave = self.playlist_atual()
        return self.music if chave == TODAS else Path(chave)

    def mostrar_musicas(self):
        if not self.ipod:
            return
        self.modelo.clear()
        chave = self.playlist_atual()
        base = self.music if chave == TODAS else Path(chave)
        if not base.is_dir():
            return
        arquivos = sorted((p for p in base.rglob("*") if p.is_file() and p.suffix.lower() in shuffle_sync.EXTENSOES),
                          key=lambda p: str(p.relative_to(self.music)).lower())
        for arq in arquivos:
            titulo, artista = shuffle_sync.ler_tags(arq) if arq.suffix.lower() == ".mp3" else (None, None)
            playlist = arq.parent.name if arq.parent != self.music else "—"
            self.modelo.append([titulo or arq.stem, artista or "", playlist, tamanho_legivel(arq.stat().st_size), str(arq)])
        total = len(arquivos)
        self.status.set_text(f"{total} música{'s' if total != 1 else ''}")

    def atualizar_espaco(self):
        try:
            uso = shutil.disk_usage(self.ipod)
        except OSError:
            return
        self.barra.set_value(uso.used / uso.total)
        self.espaco.set_text(f"{tamanho_legivel(uso.free)} livres de {tamanho_legivel(uso.total)}")

    def verificar_virus(self):
        self.virus = [n for n in SUSPEITOS if (self.ipod / n).exists()]
        if self.virus:
            self.bt_virus.set_tooltip_text(f"Vírus no iPod: {', '.join(self.virus)} — clique para remover")
            self.bt_virus.show()
        else:
            self.bt_virus.hide()

    # ---------- Sincronizacao em segundo plano ----------

    def trabalhar(self, descricao, tarefa, depois=None):
        if self.ocupado:
            return
        self.ocupado = True
        self.spinner.start()
        self.status.set_text(descricao)
        for bt in (self.bt_adicionar, self.bt_playlist, self.bt_remover, self.bt_ejetar):
            bt.set_sensitive(False)

        def executar():
            erro = None
            try:
                tarefa()
                shuffle_sync.sincronizar(self.ipod, voz=self.cfg.get("voz", True))
            except Exception as e:  # mostra o erro na janela
                erro = e
            GLib.idle_add(fim, erro)

        def fim(erro):
            self.ocupado = False
            self.spinner.stop()
            self.recarregar(self.playlist_atual())
            if erro:
                self.mensagem("Não foi possível atualizar o iPod", str(erro), Gtk.MessageType.ERROR)
            elif depois:
                depois()
            return False

        threading.Thread(target=executar, daemon=True).start()

    def mensagem(self, titulo, texto, tipo=Gtk.MessageType.INFO):
        dialogo = Gtk.MessageDialog(transient_for=self, modal=True, message_type=tipo, buttons=Gtk.ButtonsType.OK, text=titulo)
        dialogo.format_secondary_text(texto)
        dialogo.run()
        dialogo.destroy()

    # ---------- Acoes ----------

    def copiar(self, arquivos):
        destino = self.pasta_destino()
        validos = [Path(a) for a in arquivos if Path(a).suffix.lower() in shuffle_sync.EXTENSOES]
        if not validos:
            self.mensagem("Nenhuma música compatível", "O iPod Shuffle toca arquivos MP3 e M4A.")
            return

        def tarefa():
            for origem in validos:
                nome = INVALIDOS_FAT.sub("_", origem.name)
                GLib.idle_add(self.status.set_text, f"Copiando {nome}...")
                shutil.copyfile(origem, destino / nome)
            GLib.idle_add(self.status.set_text, "Atualizando o iPod...")

        self.trabalhar("Copiando músicas...", tarefa)

    def on_adicionar(self, _):
        dialogo = Gtk.FileChooserNative(title="Adicionar músicas ao iPod", transient_for=self, action=Gtk.FileChooserAction.OPEN,
                                        select_multiple=True, accept_label="Adicionar")
        filtro = Gtk.FileFilter()
        filtro.set_name("Músicas (MP3, M4A)")
        for ext in shuffle_sync.EXTENSOES:
            filtro.add_pattern("*" + ext)
            filtro.add_pattern("*" + ext.upper())
        dialogo.add_filter(filtro)
        if dialogo.run() == Gtk.ResponseType.ACCEPT:
            self.copiar(dialogo.get_filenames())
        dialogo.destroy()

    def on_soltar(self, _w, _ctx, _x, _y, dados, _info, _tempo):
        if not self.ipod or self.ocupado:
            return
        arquivos = []
        for uri in dados.get_uris():
            caminho = Path(unquote(urlparse(uri).path))
            if caminho.is_dir():
                arquivos += [str(p) for p in sorted(caminho.rglob("*")) if p.is_file()]
            else:
                arquivos.append(str(caminho))
        self.copiar(arquivos)

    def on_nova_playlist(self, _):
        dialogo = Gtk.Dialog(title="Nova playlist", transient_for=self, modal=True)
        dialogo.add_buttons("Cancelar", Gtk.ResponseType.CANCEL, "Criar", Gtk.ResponseType.OK)
        dialogo.set_default_response(Gtk.ResponseType.OK)
        entrada = Gtk.Entry(placeholder_text="Nome da playlist", activates_default=True, margin=12)
        dialogo.get_content_area().add(entrada)
        dialogo.show_all()
        resposta = dialogo.run()
        nome = INVALIDOS_FAT.sub("_", entrada.get_text().strip())
        dialogo.destroy()
        if resposta == Gtk.ResponseType.OK and nome:
            nova = self.music / nome
            nova.mkdir(exist_ok=True)
            self.carregar_playlists(str(nova))

    def on_tecla(self, _w, evento):
        if evento.keyval == Gdk.KEY_Delete:
            self.on_remover(None)
            return True
        return False

    def on_remover(self, _):
        modelo, caminhos = self.tabela.get_selection().get_selected_rows()
        arquivos = [Path(modelo[c][4]) for c in caminhos]
        chave = self.playlist_atual()
        if not arquivos:
            if chave != TODAS and not any(Path(chave).iterdir()):
                Path(chave).rmdir()  # playlist vazia: remove a pasta
                self.recarregar()
            return
        dialogo = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                                    buttons=Gtk.ButtonsType.NONE, text=f"Remover {len(arquivos)} música(s) do iPod?")
        dialogo.format_secondary_text("Os arquivos são apagados do iPod (os originais no computador não são afetados).")
        dialogo.add_buttons("Cancelar", Gtk.ResponseType.CANCEL, "Remover", Gtk.ResponseType.OK)
        dialogo.get_widget_for_response(Gtk.ResponseType.OK).get_style_context().add_class("destructive-action")
        resposta = dialogo.run()
        dialogo.destroy()
        if resposta == Gtk.ResponseType.OK:
            self.trabalhar("Removendo...", lambda: [a.unlink() for a in arquivos if a.exists()])

    def on_voz(self, sw, _):
        self.cfg["voz"] = sw.get_active()
        shuffle_sync.salvar_config(self.cfg)
        if self.ipod:
            self.trabalhar("Atualizando a voz...", lambda: None)

    def on_remover_virus(self, _):
        if not self.ipod or not self.virus:
            return
        dialogo = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.WARNING,
                                    buttons=Gtk.ButtonsType.NONE, text="Remover vírus do iPod?")
        dialogo.format_secondary_text(f"{', '.join(self.virus)} é um vírus de Windows (Houdini). "
                                      "Ele não afeta as músicas, mas pode infectar um PC com Windows.")
        dialogo.add_buttons("Cancelar", Gtk.ResponseType.CANCEL, "Remover", Gtk.ResponseType.OK)
        dialogo.get_widget_for_response(Gtk.ResponseType.OK).get_style_context().add_class("destructive-action")
        resposta = dialogo.run()
        dialogo.destroy()
        if resposta != Gtk.ResponseType.OK:
            return
        for nome in self.virus:
            alvo = self.ipod / nome
            if alvo.exists():
                alvo.unlink()
        os.sync()
        self.verificar_virus()

    def on_ejetar(self, _):
        alvo = str(self.ipod)
        os.sync()
        r = subprocess.run(["gio", "mount", "-e", alvo], capture_output=True, text=True)
        if r.returncode != 0:
            self.mensagem("Não foi possível ejetar", r.stderr.strip() or "Feche os programas que usam o iPod e tente de novo.",
                          Gtk.MessageType.WARNING)
            return
        self.ipod = None
        self.recarregar()
        self.status.set_text("iPod ejetado. Pode desconectar.")


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="br.jorge.MiTunes")
        GLib.set_application_name("MiTunes")

    def do_startup(self):
        Gtk.Application.do_startup(self)
        aplicar_tema_preto()

    def do_activate(self):
        janela = self.props.active_window or Janela(self)
        janela.present()


if __name__ == "__main__":
    App().run()
