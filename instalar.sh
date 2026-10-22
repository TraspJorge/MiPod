#!/bin/sh
# Instala o MiTunes para o usuario atual (sem sudo).
set -e
ORIGEM="$(cd "$(dirname "$0")" && pwd)"
DESTINO="$HOME/.local/share/mitunes"

mkdir -p "$DESTINO" "$HOME/.local/bin" "$HOME/.local/share/applications" \
         "$HOME/.local/share/icons/hicolor/scalable/apps" "$HOME/.config/systemd/user"
cp "$ORIGEM/mitunes.py" "$ORIGEM/shuffle_sync.py" "$ORIGEM/vigia.py" "$DESTINO/"
cp "$ORIGEM/mitunes.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/mitunes.svg"

cat > "$HOME/.local/bin/mitunes" <<EOT
#!/bin/sh
exec python3 "$DESTINO/mitunes.py" "\$@"
EOT
chmod +x "$HOME/.local/bin/mitunes"

cat > "$HOME/.local/share/applications/mitunes.desktop" <<EOT
[Desktop Entry]
Type=Application
Name=MiTunes
GenericName=Gerenciador do iPod Shuffle
Comment=Coloque músicas no iPod Shuffle sem o iTunes
Exec=$HOME/.local/bin/mitunes
Icon=mitunes
Terminal=false
Categories=AudioVideo;Audio;Music;
Keywords=ipod;shuffle;musica;mp3;itunes;
StartupWMClass=mitunes.py
EOT

# Servico que atualiza o iPod sozinho ao conectar (substitui o antigo ipod-shuffle)
cat > "$HOME/.config/systemd/user/mitunes-vigia.service" <<EOT
[Unit]
Description=MiTunes: atualiza o iPod Shuffle ao conectar ou quando a pasta music muda
After=graphical-session.target

[Service]
WorkingDirectory=$DESTINO
ExecStart=/usr/bin/python3 $DESTINO/vigia.py /media/$USER/EDIPOD
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
EOT
systemctl --user disable --now ipod-shuffle.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/ipod-shuffle.service"
systemctl --user daemon-reload
systemctl --user enable --now mitunes-vigia.service
gtk-update-icon-cache -q "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
update-desktop-database -q "$HOME/.local/share/applications" 2>/dev/null || true
echo "MiTunes instalado. Abra pelo menu de aplicativos ou com o comando: mitunes"
