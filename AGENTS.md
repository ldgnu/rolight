# AGENTS.md

Instrucciones para agentes (Claude, Codex, Cursor…) que trabajen en este repo.

## Qué es esto

rolight es un launcher para tiling WMs, en **dos interfaces** que comparten lógica:

| Archivo | Qué es | Se usa en |
| --- | --- | --- |
| `rolight.py` | GTK3 + `gtk-layer-shell`. Interfaz principal, 21 modos. | sway, Hyprland, river, niri, labwc |
| `audio.py` | Modo Sonido (solo GTK): salidas, micrófonos y codec Bluetooth con `pactl` (`LC_ALL=C`). También CLI | versión GTK |
| `notifs.py` | Modo Notificaciones (solo GTK): historial de dunst + campanita de waybar. También CLI | versión GTK, waybar |
| `music.py` | Modo Música (solo GTK): minitone como motor, mpv por IPC. También CLI | versión GTK, terminal |
| `core.py` | Lógica compartida **y** script de modo de rofi | las dos |
| `rolight` | Lanzador de la versión GTK (D-Bus + daemon) | sway, Hyprland |
| `rolight-rofi` | Lanzador de la versión rofi | i3, bspwm, Openbox, GNOME |
| `rolight.rasi` | Tema de rofi | versión rofi |

## La regla que más importa

**`rolight.py` hace `import core` (línea 34) y usa 24 símbolos de `core.py`:**

```
AI_BACKEND  CACHE  POWER  TERMINAL  WEATHER_CITY  WEB_SEARCH
ai_name  ask_ai  bump_history  calc  copy  do_power  find_files
launch_app  list_apps  load_history  notify  open_url  remmina_profiles
run_rdp  run_ssh  spawn  ssh_hosts  time_message  weather
```

Consecuencia: **cualquier cambio en `core.py` puede romper la versión GTK aunque no
la toques.** Es lo único que el repo tiene acoplado entre las dos interfaces, y
la causa más común de regresiones.

`core.py` también tiene funciones que usan **solo** la versión rofi: `show_query`,
`show_home`, `handle_info`, `main`, y todo el bloque de Portapapeles / Bluetooth /
Wi-Fi / VPN / Bitwarden. Esos no afectan a GTK, pero comparten archivo.

## Antes de commitear

```sh
python test_contrato.py
```

Verifica que (a) todo lo que `rolight.py` usa de `core` sigue existiendo,
(b) ambos archivos compilan, y (c) `FIND_HIDDEN` sigue siendo opt-in.
Si tocás `core.py` y el test falla, rompiste la versión GTK.

## Invariantes

- **No cambies el comportamiento por default de algo que la GTK consume.** Si un
  cambio mejora la versión rofi pero altera la GTK, hacelo opt-in
  (`FIND_HIDDEN`, `ROLIGHT_LOCK_CMD`, `ROLIGHT_FIND_HIDDEN`) con default idéntico
  al actual.
- **No saques ni renombres símbolos de la lista de arriba.** Si necesitás otra
  cosa, agregala; no muevas la existente.
- **La versión GTK es Wayland-only** (necesita layer-shell). Si tocás el soporte
  de X11, va en `core.py`, nunca en `rolight.py`.
- **`local.py`** (~/.config/rolight/local.py, ignorado por git) es para
  personalizaciones del usuario. No lo comitees ni asumas que existe.
- Respetá el estilo: Python sin dependencias externas (solo stdlib en `core.py`),
  comentarios y mensajes en español.

## Probar sin Wayland

En una sesión X11 no se puede abrir la versión GTK. Para verificar la parte
compartida:

```sh
ROFI_RETV=0 python core.py                 # pantalla de inicio
ROFI_RETV=1 python core.py "c algo"        # modo texto (clipboard, bt, wifi, vpn, bw, calc…)
ROFI_RETV=1 ROFI_INFO="wifi-home:" python core.py   # una vista puntual
```

`ROFI_INFO` es el `kind:valor` de la fila; `handle_info` es lo que corre al
pulsar Enter.

## Notas de implementación

- `music.py` es stdlib pura y no importa `core`. minitone no tiene CLI: se usan sus
  mismas fuentes (Radio Browser, yt-dlp), su formato de canción y sus archivos
  (`~/.config/minitone/{favorites,history}.json`), y su mpv por el socket
  `/tmp/minitone-mpv-*/ipc.sock`. El historial **no** se escribe con minitone abierto
  (lo pisaría). Para probar sin UI: `python music.py status` / `play jazz`.

- Los SSID y nombres de conexión VPN pueden contener `:`, que es el separador de
  `kind:valor`. Van con `urllib.parse.quote(safe='')`.
- El historial del portapapeles se lee con **un** `copyq eval` que trae todos los
  items. Hacerlo con un `copyq read` por item tarda ~5 s en vez de ~25 ms.
- El launcher de rofi usa `-pid` propio: sin eso pelea con el lock de instancia
  única de rofi (`$XDG_RUNTIME_DIR/rofi.pid`) y le manda toggle a cualquier otro
  rofi abierto en vez de abrirse.
- `sensors()` parsea la salida **humana** de `sensors(1)`, no `sensors -u`: con
  `-u` cada label viene como `temp1_input`/`temp1_max` y el mapeo se rompe. Los
  chips además llegan con sufijo de bus (`coretemp-isa-0000`), por eso se buscan
  por prefijo y no por nombre exacto.
- Tailscale **no** aparece como perfil en NetworkManager (es un dispositivo tun),
  así que `v` lo consulta con `tailscale status --json` primero y cae a `nmcli`
  solo si no está. `BackendState` puede decir `Stopped` con el túnel de pie: lo
  que manda es tener IP de tailnet.
- **No pruebes `tailscale up` ni `tailscale down` contra la red real del usuario
  al testear**: desconecta todo su tailnet. Usá `tailscale status --json`.