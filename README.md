# MiTunes (MiPod)

Gerenciador do **iPod Shuffle 4ª geração** para Linux, no lugar do iTunes.

Jogue arquivos MP3/M4A na pasta `music` do iPod (ou use a janela do MiTunes) e eles passam a tocar.
O firmware original da Apple continua intocado: o MiTunes recria a parte que o iTunes fazia, gerando o
banco de músicas que o iPod lê (`iPod_Control/iTunes/iTunesSD`) e as vozes do VoiceOver.

## Recursos
- Janela GTK 3 (visual nativo do elementary OS): adicionar músicas (botão ou arrastar e soltar), playlists, remover, ejetar
- Subpastas de `music` viram playlists
- VoiceOver em português (espeak): o iPod fala o nome da música, artista e playlist
- Serviço que atualiza o iPod sozinho ao conectar ou quando a pasta `music` muda
- Detecta e remove o vírus `Activador.vbs` (Houdini/H-worm) se estiver no iPod

## Instalação
```sh
sh instalar.sh
```
Instala em `~/.local/share/mitunes`, cria o atalho no menu de aplicativos (comando `mitunes`) e o serviço
`mitunes-vigia` (systemd do usuário). Requer Python 3 com GTK 3 (PyGObject) e, para a voz, `espeak`.

## Arquivos
| Arquivo | Função |
|---|---|
| `mitunes.py` | Janela do MiTunes |
| `shuffle_sync.py` | Gera o `iTunesSD` e as vozes (pode ser usado sozinho: `python3 shuffle_sync.py /media/$USER/EDIPOD`) |
| `vigia.py` | Atualização automática ao conectar o iPod |
| `instalar.sh` | Instalador |

## Créditos e licença
Formato do `iTunesSD` baseado em [nims11/IPod-Shuffle-4g](https://github.com/nims11/IPod-Shuffle-4g). Licença GPL-2.0.
