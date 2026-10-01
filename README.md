# tilelight ✦

> **Tiling + Spotlight + light.** Un launcher estilo Spotlight para sway / wlroots que vive residente,
> aparece al instante y no se come los recursos. Desde una sola caja hacés casi todo.

`tilelight` es una ventana GTK3 + `gtk-layer-shell` que queda corriendo en segundo plano y se
muestra/oculta por D-Bus en milisegundos. Los resultados se actualizan mientras escribís.
También trae una versión alternativa en modo script de **rofi** (`spotlight-rofi`).

## Modos

Escribí la letra + espacio (o `Alt+letra`). Con la búsqueda vacía, `Backspace` vuelve al inicio.
Escribí `?` para ver todos los atajos.

| Tecla | Modo | Qué hace |
|---|---|---|
| — | Apps | Lanza aplicaciones `.desktop`, ordenadas por uso |
| `c` | Portapapeles | Historial con vista previa, copiar o pegar directo |
| `f` | Archivos | Búsqueda rápida en `$HOME` con `fd` |
| `g` | Web | Busca en el navegador; también abre URLs |
| `a` | IA | Pregunta a `claude` en una terminal flotante |
| `=` | Calcular | Calculadora segura (Enter copia el resultado) |
| `s` | SSH | Hosts de `~/.ssh/config` y `known_hosts` |
| `r` | Remoto | Perfiles de Remmina / RDP |
| `v` | VPN | Perfiles de FortiClient |
| `w` | Wi-Fi | Escanear y conectar con `nmcli` |
| `b` | Bluetooth | Conectar/desconectar dispositivos |
| `m` | Monitores | Perfiles de `kanshi` |
| `t` | Clima | Clima y pronóstico (wttr.in) |
| `h` | Hora | Hora local o de cualquier ciudad |
| `p` | Energía | Bloquear, suspender, reiniciar, apagar |
| `x` | Acciones | Volumen, brillo, media, capturas, wallpaper, teclado… |
| `k` | Bitwarden | Buscar y copiar contraseñas con `rbw` |
| `i` | Sistema | CPU, memoria, temperatura, disco, batería |

## Instalación

```sh
git clone git@github.com:ldgnu/tilelight.git ~/.config/rofi/spotlight
```

Dependencias base (Debian/Ubuntu):

```sh
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-gtklayershell-0.1 wl-clipboard libnotify-bin
```

Opcionales, según los modos que uses: `fd-find`, `network-manager`, `bluez`, `kanshi`, `rbw`,
`remmina`, `forticlient`, `kitty`, `claude`, `swaylock`, `flameshot`, `playerctl`,
`brightnessctl`, `variety`, `rofi` (para la versión rofi).

## Configuración en sway

```sway
exec ~/.config/rofi/spotlight/spotlight --daemon

bindsym $mod+d      exec ~/.config/rofi/spotlight/spotlight
bindsym $mod+c      exec ~/.config/rofi/spotlight/spotlight clip
bindsym $mod+n      exec ~/.config/rofi/spotlight/spotlight wifi
bindsym $mod+b      exec ~/.config/rofi/spotlight/spotlight bt
bindsym $mod+Escape exec ~/.config/rofi/spotlight/spotlight power

for_window [app_id="spotlight-ai"] floating enable, resize set 960 640, move position center
```

`spotlight <modo>` abre directo en ese modo (`clip`, `power`, `bt`, `wifi`, `monitor`, `vpn`, …).

### Ajustes

- Terminal, buscador web, comando de IA y ciudad del clima: arriba de todo en `spotlight.py`.
- Acciones rápidas (modo `x`): lista `ACTIONS` en `live.py`. Algunas apuntan a scripts propios en
  `~/.scripts/`; borralas o reemplazalas por los tuyos.
- Imagen de la pantalla de bloqueo: `~/.config/tilelight/lock.png` o la variable `TILELIGHT_LOCK_IMAGE`.
- Logs: `~/.cache/spotlight/live.log`.

## Licencia

MIT
