#!/usr/bin/env python3
"""Rolight en vivo para sway (GTK3 + gtk-layer-shell).

Queda residente: la primera vez arranca, las siguientes solo muestra/oculta
(ver el lanzador `rolight`). Los resultados se actualizan mientras escribís.

Modos (letra + espacio, o Alt+letra; Backspace con la búsqueda vacía sale).
Escribí «?» para ver la lista de atajos.
"""
import glob
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
from shutil import which as shutil_which

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, GtkLayerShell, Pango  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# sway no siempre exporta ~/.local/bin (opencode, claude, kitty, wtype, fd viven ahí)
os.environ["PATH"] = os.pathsep.join([os.path.expanduser("~/.local/bin"), os.path.expanduser("~/.cargo/bin"),
                                      os.path.expanduser("~/.opencode/bin"),
                                      os.environ.get("PATH", "")])
import core  # noqa: E402

APP_ID = "io.github.ldgnu.Rolight"
WIDTH = 720
CLIP_DIR = os.path.join(core.CACHE, "clip")
AI_SYSTEM = "Respondé en español rioplatense, breve y directo. Sin preámbulos."

# tecla, id, nombre, ícono
MODES = [
    ("c", "clip", "Portapapeles", "edit-paste"),
    ("f", "files", "Archivos", "system-file-manager"),
    ("g", "web", "Web", "web-browser"),
    ("a", "ai", "IA", "help-about"),
    ("e", "sessions", "Sesiones IA", "document-open-recent"),
    ("=", "calc", "Calcular", "accessories-calculator"),
    ("s", "ssh", "SSH", "utilities-terminal"),
    ("r", "rdp", "Remoto", "org.remmina.Remmina"),
    ("v", "vpn", "VPN", "network-vpn"),
    ("w", "wifi", "Wi-Fi", "network-wireless"),
    ("b", "bt", "Bluetooth", "bluetooth"),
    ("m", "monitor", "Monitores", "video-display"),
    ("t", "weather", "Clima", "weather-few-clouds"),
    ("h", "time", "Hora", "preferences-system-time"),
    ("p", "power", "Energía", "system-shutdown"),
    ("x", "actions", "Acciones", "system-run"),
    ("k", "bw", "Bitwarden", "dialog-password"),
    ("i", "stats", "Sistema", "utilities-system-monitor"),
]
STAT_WORDS = ("cpu", "mem", "memoria", "ram", "temp", "temperatura", "disco", "disk", "bateria", "batería",
              "battery", "sistema", "stats", "procesos", "proc", "ventilador", "fan", "swap", "carga")
# imagen para la pantalla de bloqueo (opcional); si no existe, bloquea en negro
LOCK_IMG = os.path.expanduser(os.environ.get("ROLIGHT_LOCK_IMAGE", "~/.config/rolight/lock.png"))
KB_ES = ("swaymsg input type:keyboard xkb_layout latam && swaymsg input type:keyboard xkb_variant deadtilde"
         " && swaymsg input type:keyboard xkb_model pc104; notify-send 'Teclado en Español'")
KB_EN = ("swaymsg input type:keyboard xkb_layout us && swaymsg input type:keyboard xkb_variant intl"
         " && swaymsg input type:keyboard xkb_model pc105; notify-send 'Teclado en Inglés'")
# (título, palabras clave, ícono, comando sh, sección, ¿queda abierto?)
ACTIONS = [
    ("Teclado en Español", "teclado idioma latam español keyboard", "input-keyboard", KB_ES, "Sistema", False),
    ("Teclado en Inglés", "teclado idioma us ingles english keyboard", "input-keyboard", KB_EN, "Sistema", False),
    ("Captura de área", "captura screenshot pantallazo flameshot recorte", "applets-screenshooter",
     "sleep 0.3; flameshot gui", "Sistema", False),
    ("Captura de pantalla completa", "captura screenshot pantallazo flameshot",
     "applets-screenshooter", "sleep 0.3; flameshot screen -p \"$(xdg-user-dir PICTURES)\"", "Sistema", False),
    ("Subir volumen", "volumen audio sonido mas", "audio-volume-high",
     "pactl set-sink-volume @DEFAULT_SINK@ +10%", "Audio", True),
    ("Bajar volumen", "volumen audio sonido menos", "audio-volume-low",
     "pactl set-sink-volume @DEFAULT_SINK@ -10%", "Audio", True),
    ("Silenciar audio", "mute volumen audio sonido silencio", "audio-volume-muted",
     "pactl set-sink-mute @DEFAULT_SINK@ toggle", "Audio", True),
    ("Silenciar micrófono", "mute microfono mic", "microphone-sensitivity-muted",
     "pactl set-source-mute @DEFAULT_SOURCE@ toggle", "Audio", True),
    ("Mezclador de audio", "volumen audio mixer pulsemixer", "multimedia-volume-control",
     "kitty --class rolight-ai --title Audio pulsemixer", "Audio", False),
    ("Play / Pausa", "musica media play pausa reproducir", "media-playback-start",
     "playerctl play-pause", "Medios", True),
    ("Siguiente tema", "musica media siguiente next", "media-skip-forward", "playerctl next", "Medios", True),
    ("Tema anterior", "musica media anterior previous", "media-skip-backward", "playerctl previous", "Medios", True),
    ("Subir brillo", "brillo pantalla luz", "display-brightness-high", "brightnessctl s 10%+", "Sistema", True),
    ("Bajar brillo", "brillo pantalla luz", "display-brightness-low", "brightnessctl s 10%-", "Sistema", True),
    ("Historial de notificaciones", "notificaciones dunst historial", "preferences-system-notifications",
     "$HOME/.scripts/dunst-history.sh history", "Sistema", False),
    ("Silenciar / activar notificaciones", "notificaciones dunst no molestar mute", "notifications-disabled",
     "$HOME/.scripts/dunst-history.sh mute", "Sistema", False),
    ("Bloquear pantalla", "bloquear lock swaylock", "system-lock-screen",
     f"if [ -f {json.dumps(LOCK_IMG)} ]; then swaylock -c 000000 -i {json.dumps(LOCK_IMG)}; "
     "else swaylock -f -c 000000; fi", "Sistema", False),
    ("Recargar sway", "recargar reload sway config", "view-refresh",
     "swaymsg reload; pkill -HUP kanshi", "Sistema", False),
    ("Wallpaper siguiente", "wallpaper fondo variety siguiente", "preferences-desktop-wallpaper",
     "variety -n", "Wallpaper y tema", True),
    ("Wallpaper anterior", "wallpaper fondo variety anterior", "preferences-desktop-wallpaper",
     "variety -p", "Wallpaper y tema", True),
    ("Borrar wallpaper actual", "wallpaper fondo variety borrar papelera", "user-trash",
     "variety -t; notify-send 'Se borró el wallpaper actual'", "Wallpaper y tema", True),
    ("Wallpaper a favoritos", "wallpaper fondo variety favorito", "emblem-favorite",
     "variety -f; notify-send 'Agregado a Wallpapers Favoritos'", "Wallpaper y tema", True),
    ("Pausar wallpapers", "wallpaper fondo variety pausar", "media-playback-pause",
     "variety --pause", "Wallpaper y tema", False),
    ("Reanudar wallpapers", "wallpaper fondo variety reanudar", "media-playback-start",
     "variety --resume", "Wallpaper y tema", False),
    ("Selector de tema", "tema theme colores selector", "preferences-desktop-theme",
     "$HOME/.scripts/theme-selector.sh", "Wallpaper y tema", False),
    ("Tema aleatorio", "tema theme colores random aleatorio", "preferences-desktop-theme",
     "$HOME/.scripts/theme-random.sh", "Wallpaper y tema", False),
]
MODE_BY_KEY = {m[0]: m for m in MODES}
MODE_BY_ID = {m[1]: m for m in MODES}
WORD_MODES = {"clima": "weather", "tiempo": "weather", "hora": "time", "ssh": "ssh",
              "rdp": "rdp", "vnc": "rdp", "ia": "ai", "sesiones": "sessions", "web": "web", "calc": "calc",
              "vpn": "vpn", "wireguard": "vpn", "wifi": "wifi", "bt": "bt",
              "bluetooth": "bt", "monitor": "monitor", "monitores": "monitor"}
# palabras que, escritas en la búsqueda general, sugieren entrar al modo
MODE_HINTS = {"clip": "portapapeles clipboard copiar", "files": "archivos files",
              "vpn": "vpn wireguard openvpn", "wifi": "wifi red wireless internet",
              "bt": "bluetooth auriculares mouse", "monitor": "monitores pantallas displays kanshi",
              "weather": "clima tiempo", "power": "energia apagar reiniciar",
              "ssh": "ssh servidor", "rdp": "rdp remmina escritorio remoto",
              "bw": "bitwarden contraseñas password claves usuario",
              "sessions": "sesiones claude opencode hermes agentes retomar"}
KANSHI_CONFIG = os.path.expanduser("~/.config/kanshi/config")
MONITOR_SCRIPTS = os.path.expanduser("~/.config/sway/scripts")

CSS = b"""
window { background: transparent; }
.panel {
  background: rgba(26, 29, 35, 0.985);
  border-radius: 16px;
  border: 1px solid rgba(255, 255, 255, 0.12);
}
.inputrow { padding: 6px 20px; }
.glyph { font-family: "JetBrainsMono Nerd Font"; font-size: 20px; color: #9B9FA9; }
.query, .query:focus {
  font-family: "SF Pro Display"; font-size: 22px; font-weight: 300;
  color: #CACCD3; background: transparent; border: none; box-shadow: none;
  padding: 12px 0; caret-color: #50A4E9;
}
.ghost {
  font-family: "SF Pro Display"; font-size: 22px; font-weight: 300;
  color: rgba(202, 204, 211, 0.38);
}
.pill {
  background: #50A4E9; color: #ffffff; border-radius: 8px;
  padding: 3px 10px; font-weight: 600; font-size: 13px;
}
.chips { padding: 0 18px 12px 18px; }
.chip, .chip:hover {
  background: rgba(255, 255, 255, 0.06); border: none; box-shadow: none;
  border-radius: 8px; padding: 3px 9px; color: #9B9FA9; font-size: 12px;
}
.chip:hover { background: rgba(80, 164, 233, 0.35); color: #ffffff; }
.sep { background: rgba(255, 255, 255, 0.08); min-height: 1px; }
.card { padding: 14px 22px; color: #CACCD3; font-size: 14px; }
.answer { font-size: 13px; }
list { background: transparent; padding: 4px 0 6px 0; }
row { border-radius: 9px; padding: 6px 10px; margin: 1px 8px; }
row:selected { background: rgba(80, 164, 233, 0.85); }
row:selected label { color: #ffffff; }
.title { color: #CACCD3; font-size: 14px; }
.sub { color: rgba(202, 204, 211, 0.5); font-size: 11px; }
.section {
  color: rgba(202, 204, 211, 0.45); font-size: 11px; font-weight: 700;
  padding: 8px 20px 2px 20px;
}
.footer { color: rgba(202, 204, 211, 0.4); font-size: 11px; padding: 6px 18px 9px 18px; }
.brand { color: rgba(80, 164, 233, 0.75); font-size: 11px; font-weight: 700; padding: 6px 18px 9px 0; }
"""


class Item:
    def __init__(self, title, sub="", icon="", action=None, section="", alt=None,
                 pixbuf_path=None, close=True):
        self.title, self.sub, self.icon = title, sub, icon
        self.action, self.alt, self.section = action, alt, section
        self.pixbuf_path, self.close = pixbuf_path, close


def run_bg(fn, done):
    """Corre fn en un thread y entrega el resultado en el loop de GTK."""
    def worker():
        try:
            res = fn()
        except Exception as e:  # noqa: BLE001
            res = e
        GLib.idle_add(done, res)
    threading.Thread(target=worker, daemon=True).start()


# ── Portapapeles (CopyQ) ─────────────────────────────────────────────
CLIP_JS = r"""
var dir = %s, out = [], n = Math.min(size(), 150);
for (var i = 0; i < n; i++) {
  var fmts = str(read("?", i)).split("\n"), img = "";
  for (var j = 0; j < fmts.length; j++) if (fmts[j].indexOf("image/") == 0) { img = fmts[j]; break; }
  if (img) {
    var d = read(img, i), p = dir + "/" + str(md5sum(d)) + "." + img.split("/")[1].split(";")[0];
    var f = new File(p);
    if (!f.exists()) { f.openWriteOnly(); f.write(d); f.close(); }
    out.push({i: i, k: "img", p: p});
  } else {
    out.push({i: i, k: "txt", t: str(read("text/plain", i)).substring(0, 2000)});
  }
}
JSON.stringify(out)
"""


def load_clipboard():
    os.makedirs(CLIP_DIR, exist_ok=True)
    out = subprocess.run(["copyq", "eval", "--", CLIP_JS % json.dumps(CLIP_DIR)],
                         capture_output=True, text=True, timeout=8).stdout
    return json.loads(out or "[]")


def clip_select(index, paste=False):
    subprocess.run(["copyq", "select", str(index)], timeout=3)
    if paste:
        core.spawn(["sh", "-c", "sleep 0.25; wtype -M ctrl v -m ctrl"])


# ── Wi-Fi / Bluetooth / VPN / Monitores ──────────────────────────────
def sh(cmd, timeout=10):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout


def nm_split(line):
    return [p.replace("\\:", ":") for p in re.split(r"(?<!\\):", line)]


def wifi_scan(rescan=False):
    radio = sh(["nmcli", "radio", "wifi"]).strip()
    saved = {nm_split(l)[0] for l in sh(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"]).splitlines()
             if "wireless" in l}
    rows = sh(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "dev", "wifi", "list",
               "--rescan", "yes" if rescan else "auto"], timeout=20).splitlines()
    nets = {}
    for l in rows:
        use, ssid, sig, sec = (nm_split(l) + ["", "", "0", ""])[:4]
        if not ssid:
            continue
        n = nets.setdefault(ssid, {"ssid": ssid, "signal": 0, "sec": sec, "active": False})
        n["signal"] = max(n["signal"], int(sig or 0))
        n["active"] = n["active"] or use.strip() == "*"
        n["saved"] = ssid in saved
    return radio, sorted(nets.values(), key=lambda n: (not n["active"], not n["saved"], -n["signal"]))


def bt_devices():
    powered = "Powered: yes" in sh(["bluetoothctl", "show"], 5)
    devs = []
    for l in sh(["bluetoothctl", "devices"], 5).splitlines():
        m = re.match(r"Device (\S+) (.+)", l)
        if not m:
            continue
        info = sh(["bluetoothctl", "info", m.group(1)], 5)
        icon = re.search(r"Icon: (\S+)", info)
        bat = re.search(r"Battery Percentage: \S+ \((\d+)\)", info)
        devs.append({"mac": m.group(1), "name": m.group(2), "connected": "Connected: yes" in info,
                     "icon": icon.group(1) if icon else "bluetooth", "battery": bat.group(1) if bat else ""})
    devs.sort(key=lambda d: (not d["connected"], d["name"].lower()))
    return powered, devs


def vpn_profiles():
    """Conexiones VPN/WireGuard de NetworkManager: [(nombre, tipo)], [activas]."""
    names, active = [], []
    for l in sh(["nmcli", "-t", "-f", "NAME,TYPE,ACTIVE", "connection", "show"], 10).splitlines():
        parts = nm_split(l)
        if len(parts) < 3 or parts[1] not in ("vpn", "wireguard"):
            continue
        names.append((parts[0], "WireGuard" if parts[1] == "wireguard" else "VPN"))
        if parts[2] == "yes":
            active.append(parts[0])
    return names, active


def _ago(ts):
    secs = max(0, int(time.time() - ts))
    for unit, n in (("d", 86400), ("h", 3600), ("min", 60)):
        if secs >= n:
            return f"hace {secs // n} {unit}"
    return "recién"


def _claude_sessions(limit=40):
    out = []
    files = glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl"))
    for path in sorted(files, key=os.path.getmtime, reverse=True)[:limit]:
        try:
            with open(path, "rb") as f:
                data = f.read(30_000_000)
        except OSError:
            continue
        titles = re.findall(rb'"(?:customTitle|aiTitle|summary)":"((?:[^"\\]|\\.)*)"', data)
        cwd = re.search(rb'"cwd":"((?:[^"\\]|\\.)*)"', data)
        if not titles and not cwd:
            continue
        title = json.loads(b'"' + titles[-1] + b'"') if titles else ""
        out.append(("Claude", title or "(sin título)", json.loads(b'"' + cwd.group(1) + b'"') if cwd else "",
                    os.path.getmtime(path), ["claude", "--resume", os.path.basename(path)[:-6]]))
    return out


def _sqlite_rows(path, sql):
    import sqlite3
    if not os.path.exists(path):
        return []
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
    try:
        return db.execute(sql).fetchall()
    finally:
        db.close()


def _opencode_sessions(limit=40):
    rows = _sqlite_rows(os.path.expanduser("~/.local/share/opencode/opencode.db"),
                        "SELECT id, title, directory, time_updated FROM session WHERE parent_id IS NULL "
                        f"AND time_archived IS NULL ORDER BY time_updated DESC LIMIT {limit}")
    return [("OpenCode", t or "(sin título)", d or "", u / 1000, ["opencode", "-s", i]) for i, t, d, u in rows]


def _hermes_sessions(limit=40):
    rows = _sqlite_rows(os.path.expanduser("~/.hermes/state.db"),
                        "SELECT id, title, cwd, COALESCE(ended_at, started_at) FROM sessions "
                        "WHERE parent_session_id IS NULL AND COALESCE(archived, 0) = 0 AND source != 'subagent' "
                        f"ORDER BY started_at DESC LIMIT {limit}")
    return [("Hermes", t or "(sin título)", d or "", u or 0, ["hermes", "--resume", i]) for i, t, d, u in rows]


def ai_sessions():
    """Sesiones recientes de Claude Code, OpenCode y Hermes: (herramienta, título, carpeta, ts, cmd)."""
    out = []
    for tool, fn in (("claude", _claude_sessions), ("opencode", _opencode_sessions), ("hermes", _hermes_sessions)):
        if shutil_which(tool):
            try:
                out += fn()
            except Exception as e:  # noqa: BLE001
                print(f"sesiones {tool}: {e}", file=sys.stderr)
    return sorted(out, key=lambda r: r[3], reverse=True)[:150]


def kanshi_profiles():
    try:
        text = open(KANSHI_CONFIG).read()
    except OSError:
        return []
    out = []
    for name, body in re.findall(r"profile\s+(\S+)\s*\{(.*?)\}", text, re.S):
        outputs = [l.strip() for l in body.splitlines() if l.strip().startswith("output ")]
        out.append((name, outputs))
    return out


def apply_kanshi(outputs):
    """Aplica las líneas `output ...` de un perfil kanshi con swaymsg."""
    connected = {o["name"] for o in json.loads(sh(["swaymsg", "-t", "get_outputs"], 3))}
    listed = set()
    cmds = []
    for line in outputs:
        parts = line.split()
        name = parts[1].strip('"')
        listed.add(name)
        args = " ".join(parts[2:])
        args = re.sub(r"position\s+(-?\d+),(-?\d+)", r"position \1 \2", args)
        cmds.append(f"output {name} {args}")
    for name in connected - listed:
        cmds.append(f"output {name} disable")
    # primero se prenden y después se apagan, así nunca queda todo apagado
    cmds.sort(key=lambda c: c.endswith("disable") or " disable " in c)
    for c in cmds:
        subprocess.run(["swaymsg", c], capture_output=True, timeout=5)


def glob_scripts():
    import glob
    return glob.glob(os.path.join(MONITOR_SCRIPTS, "*.sh"))


# ── Bitwarden (rbw) ──────────────────────────────────────────────────
CLIP_CLEAR_SECONDS = 30


def rbw_unlocked():
    return subprocess.run(["rbw", "unlocked"], capture_output=True, timeout=5).returncode == 0


def rbw_list():
    if not rbw_unlocked():
        return None
    rows = sh(["rbw", "list", "--fields", "id,name,user,folder"], 20).splitlines()
    out = []
    for r in rows:
        f = (r.split("\t") + ["", "", "", ""])[:4]
        out.append({"id": f[0], "name": f[1], "user": f[2], "folder": f[3]})
    return sorted(out, key=lambda e: e["name"].lower())


def copy_secret(text):
    """Copia sin que CopyQ lo guarde y lo borra a los 30 s si sigue ahí."""
    p = subprocess.Popen(["copyq", "eval", "--",
                          'copy("x-kde-passwordManagerHint", "secret", "text/plain", input())'],
                         stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    p.communicate(text.encode(), timeout=5)

    def clear():
        try:
            cur = subprocess.run(["wl-paste", "-n"], capture_output=True, text=True, timeout=2).stdout
            if cur == text:
                subprocess.run(["wl-copy", "--clear"], timeout=2)
        except Exception:  # noqa: BLE001
            pass
        return False
    GLib.timeout_add_seconds(CLIP_CLEAR_SECONDS, clear)


# ── Monitor del sistema ──────────────────────────────────────────────
def _read(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def _hwmon():
    """{nombre_hwmon: {label: °C}} y lista de ventiladores en RPM."""
    temps, fans = {}, []
    base = "/sys/class/hwmon"
    for h in sorted(os.listdir(base)):
        d = os.path.join(base, h)
        name = _read(os.path.join(d, "name"))
        for f in sorted(os.listdir(d)):
            if f.startswith("temp") and f.endswith("_input"):
                v = int(_read(os.path.join(d, f), "0") or 0) / 1000
                if v > 0:
                    label = _read(os.path.join(d, f.replace("_input", "_label")), f.split("_")[0])
                    temps.setdefault(name, {})[label] = v
            elif f.startswith("fan") and f.endswith("_input"):
                rpm = int(_read(os.path.join(d, f), "0") or 0)
                fans.append(rpm)
    return temps, fans


class Stats:
    """Lecturas con delta entre llamadas (CPU, disco, procesos)."""
    def __init__(self):
        self.prev_cpu = None
        self.prev_disk = None
        self.prev_procs = {}
        self.prev_t = None
        self.hz = os.sysconf("SC_CLK_TCK")
        self.ncpu = os.cpu_count() or 1

    def cpu(self):
        f = [int(x) for x in _read("/proc/stat").splitlines()[0].split()[1:]]
        idle, total = f[3] + f[4], sum(f)
        pct = 0.0
        if self.prev_cpu:
            di, dt = idle - self.prev_cpu[0], total - self.prev_cpu[1]
            pct = 100 * (1 - di / dt) if dt else 0
        self.prev_cpu = (idle, total)
        return pct

    def disk_io(self, dt):
        rd = wr = 0
        for l in _read("/proc/diskstats").splitlines():
            p = l.split()
            if re.match(r"^(nvme\d+n\d+|sd[a-z])$", p[2]):
                rd += int(p[5]) * 512
                wr += int(p[9]) * 512
        res = (0, 0)
        if self.prev_disk and dt:
            res = ((rd - self.prev_disk[0]) / dt, (wr - self.prev_disk[1]) / dt)
        self.prev_disk = (rd, wr)
        return res

    def procs(self, dt):
        cur, out = {}, []
        mem_total = int(re.search(r"MemTotal:\s+(\d+)", _read("/proc/meminfo")).group(1))
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            st = _read(f"/proc/{pid}/stat")
            if not st:
                continue
            name = st[st.find("(") + 1:st.rfind(")")]
            f = st[st.rfind(")") + 2:].split()
            ticks = int(f[11]) + int(f[12])
            rss_kb = int(f[21]) * 4
            cur[pid] = ticks
            if pid in self.prev_procs and dt:
                pct = 100 * (ticks - self.prev_procs[pid]) / self.hz / dt
                out.append((pct, 100 * rss_kb / mem_total, name, pid))
        self.prev_procs = cur
        return sorted(out, reverse=True)

    def snapshot(self):
        import time
        now = time.monotonic()
        dt = (now - self.prev_t) if self.prev_t else 0
        self.prev_t = now
        mi = dict(re.findall(r"(\w+):\s+(\d+)", _read("/proc/meminfo")))
        freqs = [int(_read(f"/sys/devices/system/cpu/cpu{i}/cpufreq/scaling_cur_freq", "0") or 0)
                 for i in range(self.ncpu)]
        temps, fans = _hwmon()
        st = os.statvfs("/")
        bat = "/sys/class/power_supply/BAT0"
        return {
            "cpu": self.cpu(), "load": os.getloadavg(),
            "ghz": (sum(freqs) / len(freqs) / 1e6) if any(freqs) else 0,
            "mem_total": int(mi["MemTotal"]) * 1024, "mem_used": (int(mi["MemTotal"]) - int(mi["MemAvailable"])) * 1024,
            "swap_total": int(mi["SwapTotal"]) * 1024, "swap_used": (int(mi["SwapTotal"]) - int(mi["SwapFree"])) * 1024,
            "temps": temps, "fans": fans,
            "disk_total": st.f_blocks * st.f_frsize, "disk_used": (st.f_blocks - st.f_bfree) * st.f_frsize,
            "io": self.disk_io(dt),
            "bat": {"cap": _read(f"{bat}/capacity"), "status": _read(f"{bat}/status"),
                    "power": int(_read(f"{bat}/power_now", "0") or 0) / 1e6} if os.path.isdir(bat) else None,
            "uptime": float(_read("/proc/uptime", "0").split()[0]),
            "procs": self.procs(dt),
        }


def _gb(b):
    return f"{b / 1024 ** 3:.1f} GB"


def _rate(b):
    return f"{b / 1024 ** 2:.1f} MB/s" if b >= 1024 ** 2 else f"{b / 1024:.0f} KB/s"


def _bar(pct, width=18):
    pct = max(0, min(100, pct))
    full = round(pct / 100 * width)
    color = "#75AD47" if pct < 60 else "#D09214" if pct < 85 else "#F8747E"
    return (f"<span font_family='SF Mono' foreground='{color}'>{'█' * full}</span>"
            f"<span font_family='SF Mono' alpha='25%'>{'█' * (width - full)}</span>")


def _tcolor(t):
    return "#75AD47" if t < 65 else "#D09214" if t < 85 else "#F8747E"


def stats_markup(d):
    esc = GLib.markup_escape_text
    lines = []
    lbl = lambda t: f"<span font_family='SF Mono' alpha='60%'>{t:<6}</span>"  # noqa: E731
    lines.append(f"{lbl('CPU')} {_bar(d['cpu'])}  <b>{d['cpu']:4.0f}%</b>  "
                 f"<span alpha='60%'>{d['ghz']:.1f} GHz · carga {d['load'][0]:.2f}</span>")
    mp = 100 * d["mem_used"] / d["mem_total"]
    lines.append(f"{lbl('RAM')} {_bar(mp)}  <b>{mp:4.0f}%</b>  "
                 f"<span alpha='60%'>{_gb(d['mem_used'])} de {_gb(d['mem_total'])}</span>")
    if d["swap_total"]:
        sp = 100 * d["swap_used"] / d["swap_total"]
        lines.append(f"{lbl('Swap')} {_bar(sp)}  <b>{sp:4.0f}%</b>  "
                     f"<span alpha='60%'>{_gb(d['swap_used'])} de {_gb(d['swap_total'])}</span>")
    dp = 100 * d["disk_used"] / d["disk_total"]
    rd, wr = d["io"]
    lines.append(f"{lbl('Disco')} {_bar(dp)}  <b>{dp:4.0f}%</b>  "
                 f"<span alpha='60%'>{_gb(d['disk_total'] - d['disk_used'])} libres · ↓{_rate(rd)} ↑{_rate(wr)}</span>")
    t = d["temps"]
    parts = []
    pkg = t.get("coretemp", {}).get("Package id 0")
    if pkg:
        parts.append(f"Micro <b><span foreground='{_tcolor(pkg)}'>{pkg:.0f}°</span></b>")
    tp = t.get("thinkpad", {})
    if "GPU" in tp:
        parts.append(f"GPU <b><span foreground='{_tcolor(tp['GPU'])}'>{tp['GPU']:.0f}°</span></b>")
    nb = t.get("acpitz", {}).get("temp1")
    if nb:
        parts.append(f"Notebook <b><span foreground='{_tcolor(nb)}'>{nb:.0f}°</span></b>")
    nv = t.get("nvme", {}).get("Composite")
    if nv:
        parts.append(f"SSD <b><span foreground='{_tcolor(nv)}'>{nv:.0f}°</span></b>")
    wf = t.get("iwlwifi_1", {}).get("temp1")
    if wf:
        parts.append(f"Wi-Fi <b>{wf:.0f}°</b>")
    if parts:
        lines.append(f"{lbl('Temp')} " + "  ·  ".join(parts))
    extra = []
    fans = [f for f in d["fans"] if f]
    extra.append("Ventiladores " + (" / ".join(f"{f} rpm" for f in fans) if fans else "quietos"))
    b = d["bat"]
    if b and b["cap"]:
        est = {"Charging": "cargando", "Discharging": "con batería", "Full": "llena",
               "Not charging": "enchufada"}.get(b["status"], b["status"].lower())
        w = f" · {b['power']:.1f} W" if b["power"] else ""
        extra.append(f"Batería <b>{b['cap']}%</b> {est}{w}")
    up = int(d["uptime"])
    extra.append(f"encendida hace {up // 86400}d {up % 86400 // 3600}h {up % 3600 // 60}m"
                 if up >= 86400 else f"encendida hace {up // 3600}h {up % 3600 // 60}m")
    lines.append(f"{lbl('')} <span alpha='70%'>{esc(' · ').join(extra)}</span>")
    return "\n".join(lines)


# ── Ventana ──────────────────────────────────────────────────────────
class Rolight:
    def __init__(self, app):
        self.app = app
        self.gen = 0
        self.mode = None
        self.items = []
        self.apps = core.list_apps()
        self.clip = []
        self.weather_cache = {}
        self.sys_cache = {}
        self.remmina = sorted(core.remmina_profiles(), key=lambda r: r[0].lower())
        self.stats = Stats()
        self._stats_timer = None
        self.pix_cache = {}
        self.ai_answer = ""
        self.ai_session = None
        self.sessions_list = []
        self.confirm = None
        self._mute = False
        self._timer = None

        Gtk.Settings.get_default().set_property("gtk-icon-theme-name", "WhiteSur-dark")
        prov = Gtk.CssProvider()
        prov.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        w = self.win = Gtk.Window(application=app)
        w.set_decorated(False)
        w.set_app_paintable(True)
        visual = w.get_screen().get_rgba_visual()
        if visual:
            w.set_visual(visual)
        GtkLayerShell.init_for_window(w)
        GtkLayerShell.set_namespace(w, "rolight")
        GtkLayerShell.set_layer(w, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_anchor(w, GtkLayerShell.Edge.TOP, True)
        if hasattr(GtkLayerShell, "set_keyboard_mode"):
            GtkLayerShell.set_keyboard_mode(w, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        else:
            GtkLayerShell.set_keyboard_interactivity(w, True)
        w.connect("key-press-event", self.on_key)
        w.connect("delete-event", lambda *a: self.hide() or True)

        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.get_style_context().add_class("panel")
        panel.set_size_request(WIDTH, -1)
        self.panel = panel
        w.add(panel)

        row = Gtk.Box(spacing=12)
        row.get_style_context().add_class("inputrow")
        glyph = Gtk.Label(label="\uf002")
        glyph.get_style_context().add_class("glyph")
        self.pill = Gtk.Label()
        self.pill.get_style_context().add_class("pill")
        self.pill.set_valign(Gtk.Align.CENTER)
        self.entry = Gtk.Entry()
        self.entry.set_has_frame(False)
        self.entry.set_hexpand(True)
        self.entry.get_style_context().add_class("query")
        self.entry.connect("changed", self.on_changed)
        row.pack_start(glyph, False, False, 0)
        row.pack_start(self.pill, False, False, 0)
        # GTK3 oculta el placeholder con foco: usamos una etiqueta gris superpuesta
        self.ghost = Gtk.Label(xalign=0)
        self.ghost.get_style_context().add_class("ghost")
        self.ghost.set_halign(Gtk.Align.START)
        overlay = Gtk.Overlay()
        overlay.add(self.entry)
        overlay.add_overlay(self.ghost)
        overlay.set_overlay_pass_through(self.ghost, True)
        row.pack_start(overlay, True, True, 0)
        panel.pack_start(row, False, False, 0)

        self.chips = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.chips.get_style_context().add_class("chips")
        chip_rows = [Gtk.Box(spacing=6) for _ in range((len(MODES) + 6) // 7)]
        for r in chip_rows:
            self.chips.pack_start(r, False, False, 0)
        for n, (key, mid, name, _) in enumerate(MODES):
            b = Gtk.Button()
            lbl = Gtk.Label()
            lbl.set_markup(f"<span foreground='#50A4E9' weight='bold'>{GLib.markup_escape_text(key)}</span>  {name}")
            b.add(lbl)
            b.get_style_context().add_class("chip")
            b.set_can_focus(False)
            b.connect("clicked", lambda _b, m=mid: self.set_mode(m))
            chip_rows[n // 7].pack_start(b, False, False, 0)
        panel.pack_start(self.chips, False, False, 0)

        self.sep1 = self._sep()
        panel.pack_start(self.sep1, False, False, 0)

        self.card = Gtk.Label(xalign=0)
        self.card.set_line_wrap(True)
        self.card.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.card.set_selectable(True)
        self.card.set_can_focus(False)
        self.card.get_style_context().add_class("card")
        self.card_scroll = Gtk.ScrolledWindow()
        self.card_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.card_scroll.set_propagate_natural_height(True)
        self.card_scroll.set_max_content_height(320)
        self.card_scroll.add(self.card)
        panel.pack_start(self.card_scroll, False, False, 0)
        self.sep2 = self._sep()
        panel.pack_start(self.sep2, False, False, 0)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.BROWSE)
        self.listbox.set_header_func(self._header)
        self.listbox.connect("row-activated", lambda _l, r: self.activate(r.item, False))
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_propagate_natural_height(True)
        self.scroll.set_max_content_height(470)
        self.scroll.add(self.listbox)
        panel.pack_start(self.scroll, False, False, 0)

        self.footer = Gtk.Label(xalign=0)
        self.footer.get_style_context().add_class("footer")
        self.footer_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.footer_box.pack_start(self._sep(), False, False, 0)
        foot = Gtk.Box()
        foot.pack_start(self.footer, True, True, 0)
        brand = Gtk.Label(label="✦ rolight", xalign=1)
        brand.get_style_context().add_class("brand")
        foot.pack_end(brand, False, False, 0)
        self.footer_box.pack_start(foot, False, False, 0)
        panel.pack_start(self.footer_box, False, False, 0)

    # ── utilidades de UI ─────────────────────────────────────────────
    def _sep(self):
        s = Gtk.Box()
        s.get_style_context().add_class("sep")
        return s

    def _header(self, row, before):
        sec = row.item.section
        if sec and (before is None or before.item.section != sec):
            lbl = Gtk.Label(label=sec.upper(), xalign=0)
            lbl.get_style_context().add_class("section")
            row.set_header(lbl)
        else:
            row.set_header(None)

    def _image(self, it):
        size = 32
        if it.pixbuf_path:
            pb = self.pix_cache.get(it.pixbuf_path)
            if pb is None:
                try:
                    pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(it.pixbuf_path, 72, 48, True)
                except GLib.Error:
                    pb = False
                self.pix_cache[it.pixbuf_path] = pb
            if pb:
                return Gtk.Image.new_from_pixbuf(pb)
        icon = it.icon or "application-x-executable"
        if isinstance(icon, Gio.Icon):
            img = Gtk.Image.new_from_gicon(icon, Gtk.IconSize.DND)
        elif icon.startswith("/"):
            try:
                img = Gtk.Image.new_from_pixbuf(
                    GdkPixbuf.Pixbuf.new_from_file_at_scale(icon, size, size, True))
            except GLib.Error:
                img = Gtk.Image.new_from_icon_name("application-x-executable", Gtk.IconSize.DND)
        else:
            img = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.DND)
        img.set_pixel_size(size)
        return img

    def _row(self, it):
        r = Gtk.ListBoxRow()
        r.item = it
        box = Gtk.Box(spacing=12)
        box.pack_start(self._image(it), False, False, 0)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        texts.set_valign(Gtk.Align.CENTER)
        t = Gtk.Label(label=it.title, xalign=0)
        t.set_ellipsize(Pango.EllipsizeMode.END)
        t.get_style_context().add_class("title")
        texts.pack_start(t, False, False, 0)
        if it.sub:
            s = Gtk.Label(label=it.sub, xalign=0)
            s.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            s.get_style_context().add_class("sub")
            texts.pack_start(s, False, False, 0)
        box.pack_start(texts, True, True, 0)
        r.add(box)
        return r

    def render(self, items, card=None, footer=None):
        self.items = items
        for child in self.listbox.get_children():
            self.listbox.remove(child)
        for it in items:
            self.listbox.add(self._row(it))
        self.listbox.show_all()
        first = self.listbox.get_row_at_index(0)
        if first:
            self.listbox.select_row(first)
        self.scroll.set_visible(bool(items))
        self.set_card(card)
        self.footer.set_text(footer or self.default_footer())
        self.footer_box.set_visible(bool(items) or bool(card))
        GLib.idle_add(self._fit)

    def _fit(self):
        """gtk-layer-shell no respeta resize(): fijamos el mínimo de cada scroll."""
        for scroll, child, cap in ((self.scroll, self.listbox, 470), (self.card_scroll, self.card, 320)):
            if scroll.get_visible():
                _, nat = child.get_preferred_height_for_width(WIDTH)
                scroll.set_min_content_height(min(nat, cap))
            else:
                scroll.set_min_content_height(0)
        self.win.resize(WIDTH, 1)
        return False

    def set_card(self, markup):
        self.card.set_markup(markup or "")
        self.card_scroll.set_visible(bool(markup))
        self.sep2.set_visible(bool(markup) and self.scroll.get_visible())

    def default_footer(self):
        if self.mode:
            return "↵ elegir · Ctrl+↵ alternativa · ⌫ salir del modo · Esc cerrar"
        return "↵ abrir · Ctrl+↵ alternativa · ? atajos · Esc cerrar"

    def move(self, delta):
        rows = self.listbox.get_children()
        if not rows:
            return
        cur = self.listbox.get_selected_row()
        idx = cur.get_index() if cur else -1
        nxt = self.listbox.get_row_at_index(max(0, min(len(rows) - 1, idx + delta)))
        self.listbox.select_row(nxt)
        nxt.grab_focus()
        self.entry.grab_focus_without_selecting()

    # ── mostrar / ocultar ────────────────────────────────────────────
    def toggle(self):
        if self.win.get_visible():
            self.hide()
        else:
            self.show()

    def show(self):
        try:
            outs = json.loads(subprocess.run(["swaymsg", "-t", "get_outputs"],
                                             capture_output=True, text=True, timeout=1).stdout)
            h = next(o["rect"]["height"] for o in outs if o.get("focused"))
        except Exception:  # noqa: BLE001
            h = 1080
        GtkLayerShell.set_margin(self.win, GtkLayerShell.Edge.TOP, int(h * 0.2))
        self.reset()
        self.win.show_all()
        self.update()
        self.entry.grab_focus()
        run_bg(core.list_apps, self._apps_loaded)
        run_bg(lambda: sorted(core.remmina_profiles(), key=lambda r: r[0].lower()),
               lambda r: setattr(self, "remmina", r) if isinstance(r, list) else None)
        run_bg(load_clipboard, self._clip_loaded)

    def hide(self):
        self.win.hide()
        self.reset()

    def reset(self):
        self.mode, self.confirm, self.ai_answer = None, None, ""
        self._mute = True
        self.entry.set_text("")
        self._mute = False

    def _apps_loaded(self, res):
        if isinstance(res, list):
            self.apps = res

    def _clip_loaded(self, res):
        if isinstance(res, list):
            self.clip = res
            if self.mode == "clip" or (self.mode is None and len(self.query()) >= 3):
                self.update()

    # ── entrada ──────────────────────────────────────────────────────
    def query(self):
        return self.entry.get_text().strip()

    def set_mode(self, mid, rest=""):
        self.mode, self.confirm, self.ai_answer = mid, None, ""
        self._mute = True
        self.entry.set_text(rest)
        self.entry.set_position(-1)
        self._mute = False
        self.entry.grab_focus_without_selecting()
        self.update()

    def on_changed(self, _e):
        if self._mute:
            return
        self.confirm = None
        text = self.entry.get_text()
        if self.mode is None:
            m = re.match(r"^([a-z=])\s(.*)$", text, re.S) or re.match(r"^([=/])(.*)$", text, re.S)
            if m and (m.group(1) in MODE_BY_KEY or m.group(1) == "/"):
                key = "f" if m.group(1) == "/" else m.group(1)
                return self.set_mode(MODE_BY_KEY[key][1], m.group(2))
            m = re.match(r"^(\w+)\s(.*)$", text)
            if m and m.group(1).lower() in WORD_MODES:
                return self.set_mode(WORD_MODES[m.group(1).lower()], m.group(2))
        if self.mode == "ai":
            self.ai_answer = ""
        self.update()

    def on_key(self, _w, ev):
        k = ev.keyval
        ctrl = bool(ev.state & Gdk.ModifierType.CONTROL_MASK)
        alt = bool(ev.state & Gdk.ModifierType.MOD1_MASK)
        if k == Gdk.KEY_Escape:
            self.hide()
            return True
        if k in (Gdk.KEY_Down, Gdk.KEY_Tab) or (ctrl and k in (Gdk.KEY_n, Gdk.KEY_j)):
            self.move(1)
            return True
        if k in (Gdk.KEY_Up, Gdk.KEY_ISO_Left_Tab) or (ctrl and k in (Gdk.KEY_p, Gdk.KEY_k)):
            self.move(-1)
            return True
        if k in (Gdk.KEY_Page_Down, Gdk.KEY_Page_Up):
            self.move(6 if k == Gdk.KEY_Page_Down else -6)
            return True
        if k in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            row = self.listbox.get_selected_row()
            if row:
                self.activate(row.item, ctrl)
            return True
        if k == Gdk.KEY_BackSpace and self.mode and not self.entry.get_text():
            self.mode, self.confirm = None, None
            self.update()
            return True
        if alt:
            ch = chr(Gdk.keyval_to_unicode(k) or 0).lower()
            if ch in MODE_BY_KEY:
                self.set_mode(MODE_BY_KEY[ch][1])
                return True
        return False

    def activate(self, it, alt):
        fn = it.alt if (alt and it.alt) else it.action
        if fn is None:
            return
        keep = fn() is False or not it.close
        if not keep:
            self.hide()

    # ── resultados ───────────────────────────────────────────────────
    def update(self):
        self.gen += 1
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = None
        q = self.query()
        mode = self.mode
        if mode:
            _, _, name, _ = MODE_BY_ID[mode]
            self.pill.set_text(name)
            self.pill.show()
        else:
            self.pill.hide()
        self.chips.set_visible(False)
        self.ghost.set_text(self.placeholder())
        self.ghost.set_visible(not self.entry.get_text())
        if self.confirm:
            return self.render(self.confirm[1], self.confirm[0])
        getattr(self, f"mode_{mode or 'mixed'}")(q)
        self.sep1.set_visible(bool(self.items) or self.card_scroll.get_visible())

    def placeholder(self):
        return {
            None: self.greeting(),
            "clip": "Buscar en el portapapeles (img = solo imágenes)",
            "ssh": "Servidor o user@host", "rdp": "Equipo o perfil de Remmina",
            "files": "Nombre de archivo o carpeta", "web": "Buscar en Google o URL",
            "ai": f"Preguntale algo a {core.ai_name()} y ↵", "calc": "Ej. 12*3+4, 2^10, 15% de 200",
            "weather": "Ciudad (vacío = donde estás)", "time": "Ciudad (vacío = acá)",
            "power": "Bloquear, suspender, reiniciar…",
            "vpn": "Nombre de la VPN", "sessions": "Buscar sesión de Claude, OpenCode o Hermes", "wifi": "Nombre de la red",
            "bt": "Dispositivo bluetooth", "monitor": "Perfil de monitores",
            "actions": "Teclado, captura, volumen, wallpaper, tema…",
            "bw": "Buscar en Bitwarden",
            "stats": "Filtrar procesos…",
        }[self.mode]

    @staticmethod
    def greeting():
        h = __import__("datetime").datetime.now().hour
        saludo = "Buenos días" if 5 <= h < 13 else "Buenas tardes" if h < 20 else "Buenas noches"
        return f"{saludo}, Javi…"

    def later(self, ms, fn):
        gen = self.gen

        def fire():
            self._timer = None
            if gen == self.gen:
                fn(gen)
            return False
        self._timer = GLib.timeout_add(ms, fire)

    def deliver(self, gen, fn):
        """Wrapper para resultados async: descarta si el usuario siguió escribiendo."""
        def done(res):
            if gen == self.gen and self.win.get_visible():
                fn(res)
            return False
        return done

    # ── items reutilizables ──────────────────────────────────────────
    def app_items(self, q, limit=8):
        hist = core.load_history()
        ql = q.lower()
        scored = []
        for name, did, path, icon, sub, kw in self.apps:
            n = name.lower()
            if not ql:
                s = hist.get("app:" + did, 0)
                if not s:
                    continue
            elif n.startswith(ql):
                s = 100
            elif any(w.startswith(ql) for w in re.split(r"[\s\-_.()]+", n)):
                s = 80
            elif ql in n:
                s = 60
            elif ql in kw.lower() or ql in sub.lower():
                s = 30
            else:
                continue
            s += min(hist.get("app:" + did, 0), 25)
            scored.append((s, name, did, path, icon, sub))
        scored.sort(key=lambda x: (-x[0], x[1].lower()))
        sec = "Aplicaciones" if ql else "Recientes"
        return [Item(n, sub, icon, self._launch(did, path), sec)
                for _, n, did, path, icon, sub in scored[:limit]]

    def _launch(self, did, path):
        def go():
            core.bump_history("app:" + did)
            core.launch_app(did, path)
        return go

    def power_items(self, q=""):
        ql = q.lower()
        out = []
        for title, act, icon, kw in core.POWER:
            if ql and ql not in title.lower() and ql not in kw:
                continue
            out.append(Item(title, "sistema", icon, self._power(act, title, icon), "Sistema"))
        return out

    def _power(self, act, title, icon):
        def go():
            if act in ("reboot", "poweroff", "logout"):
                self.confirm = (f"¿Seguro que querés <b>{title.lower()}</b>?", [
                    Item(f"Sí, {title.lower()}", "", icon, lambda: core.do_power(act)),
                    Item("Cancelar", "", "dialog-cancel", self._cancel_confirm, close=False),
                ])
                self.update()
                return False
            core.do_power(act)
        return go

    def _cancel_confirm(self):
        self.confirm = None
        self.update()
        return False

    def ssh_items(self, q, limit=20):
        ql = q.lower()
        hosts = [h for h in core.ssh_hosts() if ql in h.lower()]
        return [Item(h, "ssh · abrir en kitty", "utilities-terminal",
                     lambda h=h: core.run_ssh(h), "Servidores") for h in hosts[:limit]]

    def remmina_items(self, q, limit=80):
        words = q.lower().split()
        out = []
        for n, proto, srv, path, group in self.remmina:
            hay = f"{n} {srv} {group} {proto}".lower()
            if words and not all(w in hay for w in words):
                continue
            sub = " · ".join(filter(None, [proto, srv, group]))
            out.append(Item(n, sub, "org.remmina.Remmina", lambda p=path: core.run_rdp(p), "Remmina"))
            if len(out) >= limit:
                break
        return out

    def fallback_items(self, q):
        return [
            Item(f"Buscar «{q}» en la web", "Google", "web-browser", lambda: self._web(q), "Más"),
            Item(f"Preguntar a la IA: {q}", f"{core.ai_name()} · ↵ responde acá · Ctrl+↵ terminal",
                 "help-about", lambda: self._ask_inline(q), "Más",
                 alt=lambda: core.ask_ai(q)),
        ]

    def _web(self, q):
        core.open_url(core.WEB_SEARCH.format(urllib.parse.quote_plus(q)))

    def calc_card(self, q):
        res = core.calc(q)
        if res is None:
            return None, []
        card = (f"<span alpha='60%'>{GLib.markup_escape_text(q)} =</span>\n"
                f"<span size='xx-large' weight='bold'>{GLib.markup_escape_text(res)}</span>")
        return card, [Item(f"= {res}", "↵ copiar resultado", "accessories-calculator",
                           lambda: core.copy(res), "Calculadora")]

    def file_items(self, paths):
        home = os.path.expanduser("~")
        out = []
        for p in paths:
            p = p.rstrip("/")
            if os.path.isdir(p):
                icon = "folder"
            else:
                ctype, _ = Gio.content_type_guess(p, None)
                icon = Gio.content_type_get_icon(ctype)
            parent = os.path.dirname(p).replace(home, "~", 1)
            out.append(Item(os.path.basename(p), parent, icon,
                            lambda p=p: core.spawn(["xdg-open", p]), "Archivos",
                            alt=lambda p=p: core.spawn(["xdg-open", os.path.dirname(p)])))
        return out

    def clip_items(self, q, limit=60, section="Portapapeles"):
        ql = q.lower()
        only_img = ql in ("img", "imagen", "imagenes", "imágenes", "image")
        out = []
        for c in self.clip:
            if c["k"] == "img":
                if ql and not only_img:
                    continue
                try:
                    _, w, h = GdkPixbuf.Pixbuf.get_file_info(c["p"])
                    sub = f"Imagen {w}×{h}"
                except Exception:  # noqa: BLE001
                    sub = "Imagen"
                out.append(Item("Imagen", sub + " · ↵ copiar · Ctrl+↵ copiar y pegar", "image-x-generic",
                                self._clip(c["i"]), section, alt=self._clip(c["i"], True),
                                pixbuf_path=c["p"]))
            else:
                t = c.get("t", "")
                if only_img or (ql and ql not in t.lower()):
                    continue
                first = " ".join(t.split())[:120] or "(vacío)"
                lines = t.count("\n") + 1
                sub = f"{lines} líneas · {len(t)} caracteres" if lines > 1 else f"{len(t)} caracteres"
                out.append(Item(first, sub, "edit-paste", self._clip(c["i"]), section,
                                alt=self._clip(c["i"], True)))
            if len(out) >= limit:
                break
        return out

    def _clip(self, index, paste=False):
        def go():
            clip_select(index, paste)
            if not paste:
                core.notify("Copiado al portapapeles")
        return go

    # ── modos ────────────────────────────────────────────────────────
    def mode_mixed(self, q):
        if not q:
            return self.render([])
        if q.lower() in STAT_WORDS:
            return self.mode_stats("")
        if q == "?":
            return self.render([Item(name, f"«{key}» + espacio  ·  Alt+{key}", icon,
                                     lambda m=mid: self.set_mode(m) or False, "Atajos", close=False)
                                for key, mid, name, icon in MODES]
                               + [Item("Buscar archivos rápido", "«/» + nombre", "system-file-manager",
                                       lambda: self.set_mode("files") or False, "Atajos", close=False)],
                               None, "↵ entrar al modo · Ctrl+↵ alternativa en cada resultado · Esc cerrar")
        card, items = self.calc_card(q)
        if re.match(r"^(https?://|www\.)\S+$|^[\w-]+(\.[\w-]+)+(/\S*)?$", q) and not card:
            url = q if q.startswith("http") else "https://" + q
            items.append(Item(f"Abrir {url}", "navegador predeterminado", "web-browser",
                              lambda: core.open_url(url), "Web"))
        ql = q.lower()
        if len(ql) >= 2:
            for mid, words in MODE_HINTS.items():
                key, _, name, icon = MODE_BY_ID[mid]
                if any(w.startswith(ql) for w in words.split()):
                    items.append(Item(name, f"modo · atajo «{key}» + espacio o Alt+{key}", icon,
                                      lambda m=mid: self.set_mode(m) or False, "Modos", close=False))
        items += self.app_items(q)
        items += self.action_items(q, 5) if len(q) >= 3 else []
        items += self.power_items(q) if len(q) >= 3 else []
        items += self.ssh_items(q, 4) if len(q) >= 2 else []
        items += self.remmina_items(q, 5) if len(q) >= 2 else []
        if len(q) >= 3:
            items += self.clip_items(q, 3)
        base = items + self.fallback_items(q)
        self.render(base, card)
        if len(q) >= 3:
            self.later(220, lambda gen: run_bg(
                lambda: core.find_files(q), self.deliver(gen, lambda paths: self.render(
                    items + (self.file_items(paths[:6]) if isinstance(paths, list) else [])
                    + self.fallback_items(q), card))))

    def mode_clip(self, q):
        items = self.clip_items(q)
        if not self.clip:
            return self.render([], "<span alpha='60%'>Cargando portapapeles…</span>")
        self.render(items, None if items else "<span alpha='60%'>Nada coincide</span>",
                    "↵ copiar · Ctrl+↵ copiar y pegar · «img» = solo imágenes · ⌫ salir")

    def mode_ssh(self, q):
        items = self.ssh_items(q)
        if q and q not in core.ssh_hosts():
            items.insert(0, Item(f"Conectar a {q}", "ssh", "utilities-terminal",
                                 lambda: core.run_ssh(q), "Servidores"))
        self.render(items)

    def mode_rdp(self, q):
        items = self.remmina_items(q, 80)
        if q:
            target = q if "://" in q else f"rdp://{q}"
            quick = [Item(f"RDP a {q}", "conexión nueva · pide credenciales", "org.remmina.Remmina",
                          lambda: core.run_rdp(target), "Conexión nueva"),
                     Item(f"VNC a {q}", "conexión nueva", "org.remmina.Remmina",
                          lambda: core.run_rdp(f"vnc://{q}"), "Conexión nueva")]
            items = items + quick if items else quick
        items.append(Item("Abrir Remmina", "", "org.remmina.Remmina",
                          lambda: core.spawn(["remmina"]), "Opciones"))
        self.render(items, None, "↵ conectar con el perfil guardado · ⌫ salir")

    def mode_files(self, q):
        if len(q) < 2:
            return self.render([], "<span alpha='60%'>Escribí al menos 2 letras…</span>")
        self.render([], "<span alpha='60%'>Buscando…</span>")
        self.later(150, lambda gen: run_bg(lambda: core.find_files(q), self.deliver(gen, lambda paths: self.render(
            self.file_items(paths) if isinstance(paths, list) else [],
            None if paths else f"Sin archivos para «{GLib.markup_escape_text(q)}»",
            "↵ abrir · Ctrl+↵ abrir carpeta · ⌫ salir"))))

    def mode_web(self, q):
        if not q:
            return self.render([])
        items = []
        if re.match(r"^(https?://|www\.)\S+$|^[\w-]+(\.[\w-]+)+(/\S*)?$", q):
            url = q if q.startswith("http") else "https://" + q
            items.append(Item(f"Abrir {url}", "", "web-browser", lambda: core.open_url(url), "Web"))
        items.append(Item(f"Buscar «{q}»", "Google", "web-browser", lambda: self._web(q), "Web"))
        self.render(items)

    def mode_ai(self, q):
        if self.ai_answer:
            ans = self.ai_answer
            return self.render([
                Item("Copiar respuesta", "", "edit-copy", lambda: core.copy(ans)),
                Item("Seguir la charla en terminal", core.ai_name(), "utilities-terminal",
                     lambda s=self.ai_session: core.ask_ai(q, s)),
            ], f"<span size='small'>{GLib.markup_escape_text(ans)}</span>",
                "↵ elegir · escribí para preguntar otra cosa · Esc cerrar")
        if not q:
            return self.render([])
        self.render([Item(f"Preguntar: {q}", f"↵ responde acá · Ctrl+↵ abre {core.ai_name()} en terminal",
                          "help-about", lambda: self._ask_inline(q), alt=lambda: core.ask_ai(q))])

    def _ask_inline(self, q):
        if self.mode != "ai":
            self.set_mode("ai", q)
        self.render([], "<span alpha='60%'>Pensando…</span>", "Esc cerrar")
        gen = self.gen

        def ask():
            home = os.path.expanduser("~")
            if core.AI_BACKEND == "opencode":
                r = subprocess.run(["opencode", "run", "--format", "json", f"{AI_SYSTEM}\n\n{q}"],
                                   capture_output=True, text=True, timeout=180, cwd=home)
                parts, sid = [], None
                for line in r.stdout.splitlines():
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    sid = ev.get("sessionID") or sid
                    if ev.get("type") == "text":
                        parts.append(ev.get("part", {}).get("text", ""))
                return "".join(parts).strip() or r.stderr.strip(), sid
            r = subprocess.run(["claude", "-p", q, "--append-system-prompt", AI_SYSTEM],
                               capture_output=True, text=True, timeout=120, cwd=home)
            return (r.stdout or r.stderr).strip(), None

        def done(res):
            if isinstance(res, Exception):
                self.ai_answer, self.ai_session = f"Error: {res}", None
            else:
                self.ai_answer, self.ai_session = res
            self.mode_ai(q)
        run_bg(ask, self.deliver(gen, done))
        return False

    def mode_calc(self, q):
        card, items = self.calc_card(q)
        self.render(items, card or (q and "<span alpha='60%'>…</span>"))

    def mode_weather(self, q):
        city = q
        if city in self.weather_cache:
            return self._show_weather(city, self.weather_cache[city])
        self.render([], "<span alpha='60%'>Consultando el clima…</span>")
        self.later(450 if q else 0, lambda gen: run_bg(
            lambda: core.weather(city), self.deliver(gen, lambda res: self._show_weather(city, res))))

    def _show_weather(self, city, res):
        if isinstance(res, Exception):
            return self.render([], f"No pude obtener el clima ({GLib.markup_escape_text(type(res).__name__)})")
        self.weather_cache[city] = res
        msg, days = res
        loc = urllib.parse.quote(city or core.WEATHER_CITY)
        full = lambda: core.spawn(core.TERMINAL + ["--class", "rolight-ai", "--title", "Clima", "--hold",  # noqa: E731
                                                   "curl", "-s", f"https://wttr.in/{loc}?lang=es"])
        self.render([Item(f"{d}   {t}", f"{desc} · lluvia {rain}%", "weather-few-clouds", full, "Pronóstico")
                     for d, t, desc, rain in days], msg, "↵ pronóstico completo · ⌫ salir")

    def mode_time(self, q):
        msg = core.time_message(q)
        hhmm = re.search(r"\d\d:\d\d", msg)
        items = [Item(f"Copiar {hhmm.group()}", "", "edit-copy", lambda: core.copy(hhmm.group()))] if hhmm else []
        self.render(items, msg)

    def action_items(self, q, limit=100):
        words = q.lower().split()
        out = []
        for title, kw, icon, cmd, sec, keep in ACTIONS:
            hay = f"{title.lower()} {kw}"
            if all(w in hay for w in words):
                out.append(Item(title, sec, icon, lambda c=cmd: core.spawn(["sh", "-c", c]),
                                "Acciones" if limit < 100 else sec, close=not keep))
            if len(out) >= limit:
                break
        return out

    def mode_actions(self, q):
        self.render(self.action_items(q), None, "↵ ejecutar · volumen/brillo/música/wallpaper quedan abiertos · ⌫ salir")

    def mode_bw(self, q):
        if not shutil_which("rbw"):
            return self.render([], "rbw no está instalado")

        def show(res):
            if isinstance(res, Exception):
                return self.render([], f"Error con Bitwarden: {GLib.markup_escape_text(str(res))}")
            if res is None:
                return self.render([Item("Desbloquear Bitwarden", "te pide la contraseña maestra", "dialog-password",
                                         self._bw_unlock, close=False)], "<b>Bitwarden está bloqueado</b>")
            words = q.lower().split()
            items = []
            first = None
            for e in res:
                hay = f"{e['name']} {e['user']} {e['folder']}".lower()
                if words and not all(w in hay for w in words):
                    continue
                sub = " · ".join(filter(None, [e["user"], e["folder"]]))
                first = first or e
                items.append(Item(e["name"], sub, "dialog-password", self._bw_copy(e, "password"),
                                  "Bóveda", alt=self._bw_copy(e, "user")))
                if len(items) >= 60:
                    break
            items += [
                Item(f"Copiar código 2FA de {first['name']}", "TOTP", "appointment-soon",
                     self._bw_copy(first, "totp"), "Opciones"),
            ] if first else []
            items += [
                Item("Sincronizar bóveda", "rbw sync", "view-refresh", self._bw_sync, "Opciones", close=False),
                Item("Bloquear Bitwarden", "rbw lock", "system-lock-screen",
                     lambda: (subprocess.run(["rbw", "lock"], timeout=5), self.sys_cache.pop("bw", None)), "Opciones"),
            ] if items or not words else []
            self.render(items, None if items else "<span alpha='60%'>Nada coincide</span>",
                        f"↵ copiar contraseña · Ctrl+↵ copiar usuario · se borra a los {CLIP_CLEAR_SECONDS}s · ⌫ salir")
        self.async_mode("bw", rbw_list, show, "Abriendo bóveda…")

    def _bw_copy(self, e, what):
        def go():
            if what == "user":
                if e["user"]:
                    copy_secret(e["user"])
                    core.notify("Usuario copiado", e["name"])
                return
            cmd = ["rbw", "code", e["id"]] if what == "totp" else ["rbw", "get", e["id"]]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            val = r.stdout.rstrip("\n")
            if r.returncode or not val:
                core.notify("Bitwarden", (r.stderr or "sin dato").strip()[:200])
                return
            copy_secret(val)
            core.notify("Código 2FA copiado" if what == "totp" else "Contraseña copiada",
                        f"{e['name']} · se borra en {CLIP_CLEAR_SECONDS}s")
        return go

    def _bw_unlock(self):
        # pinentry necesita el teclado: ocultamos el Rolight mientras pide la clave
        self.hide()

        def done(res):
            ok = not isinstance(res, Exception) and res.returncode == 0
            self.sys_cache.pop("bw", None)
            if ok:
                self.app.open_mode("bw")
            else:
                core.notify("Bitwarden", "No se pudo desbloquear")
            return False
        run_bg(lambda: subprocess.run(["rbw", "unlock"], capture_output=True, timeout=180), done)
        return None

    def _bw_sync(self):
        self.bg_cmd(["rbw", "sync"], "Bóveda sincronizada")()
        self.sys_cache.pop("bw", None)
        return False

    def mode_stats(self, q, keep_sel=False):
        d = self.stats.snapshot()
        if not d["procs"]:  # primera lectura: sin delta todavía
            d = (GLib.usleep(250000), self.stats.snapshot())[1]
        ql = q.lower()
        items = []
        for cpu, mem, name, pid in d["procs"]:
            if ql and ql not in name.lower():
                continue
            items.append(Item(name, f"CPU {cpu:.1f}%  ·  RAM {mem:.1f}%  ·  PID {pid}", "utilities-system-monitor",
                              lambda: core.spawn(core.TERMINAL + ["--title", "btop", "btop"]),
                              "Procesos que más consumen",
                              alt=lambda p=pid, n=name: self._kill_confirm(p, n)))
            if len(items) >= 8:
                break
        items.append(Item("Abrir btop", "monitor completo", "utilities-system-monitor",
                          lambda: core.spawn(core.TERMINAL + ["--title", "btop", "btop"]), "Más"))
        sel = self.listbox.get_selected_row()
        idx = sel.get_index() if (keep_sel and sel) else 0
        self.render(items, stats_markup(d), "en vivo · ↵ abrir btop · Ctrl+↵ terminar proceso · Esc cerrar")
        row = self.listbox.get_row_at_index(min(idx, len(items) - 1))
        if row:
            self.listbox.select_row(row)
        if self._stats_timer is None:
            self._stats_timer = GLib.timeout_add(1000, self._stats_tick)

    def _stats_tick(self):
        live = self.win.get_visible() and not self.confirm and (
            self.mode == "stats" or (self.mode is None and self.query().lower() in STAT_WORDS))
        if not live:
            self._stats_timer = None
            return False
        self.mode_stats(self.query() if self.mode == "stats" else "", keep_sel=True)
        return True

    def _kill_confirm(self, pid, name):
        self.confirm = (f"¿Terminar <b>{GLib.markup_escape_text(name)}</b> (PID {pid})?", [
            Item(f"Sí, terminar {name}", "kill -TERM", "process-stop",
                 lambda: subprocess.run(["kill", "-TERM", str(pid)], timeout=3)),
            Item("Cancelar", "", "dialog-cancel", self._cancel_confirm, close=False),
        ])
        self.update()
        return False

    def mode_power(self, q):
        self.render(self.power_items(q))

    # ── modos de sistema (async) ─────────────────────────────────────
    def async_mode(self, key, loader, show, loading="Cargando…"):
        """Muestra lo cacheado al instante y refresca en segundo plano."""
        cached = self.sys_cache.get(key)
        if cached is not None:
            show(cached)
        else:
            self.render([], f"<span alpha='60%'>{loading}</span>")
        gen = self.gen

        def done(res):
            if not isinstance(res, Exception):
                self.sys_cache[key] = res
            if gen == self.gen and self.win.get_visible():
                show(res)
        run_bg(loader, done)

    def bg_cmd(self, cmd, ok_msg, refresh=True):
        def go():
            def run():
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
                return r.returncode, (r.stdout + r.stderr).strip()

            def done(res):
                if isinstance(res, Exception) or res[0] != 0:
                    detail = str(res) if isinstance(res, Exception) else res[1][-200:]
                    core.notify("Falló", detail)
                else:
                    core.notify(ok_msg)
                if refresh and self.win.get_visible():
                    self.update()
            run_bg(run, done)
            self.render([], "<span alpha='60%'>Procesando…</span>")
            return False
        return go

    def mode_wifi(self, q):
        def show(res):
            if isinstance(res, Exception):
                return self.render([], f"Error leyendo Wi-Fi: {GLib.markup_escape_text(str(res))}")
            radio, nets = res
            ql = q.lower()
            items = []
            if radio != "enabled":
                return self.render([Item("Encender Wi-Fi", "", "network-wireless",
                                         self.bg_cmd(["nmcli", "radio", "wifi", "on"], "Wi-Fi encendido"))])
            for n in nets:
                if ql and ql not in n["ssid"].lower():
                    continue
                s = n["signal"]
                lvl = "excellent" if s >= 75 else "good" if s >= 50 else "ok" if s >= 25 else "weak"
                sub = " · ".join(filter(None, ["conectado" if n["active"] else ("guardada" if n["saved"] else ""),
                                               f"{s}%", n["sec"] or "abierta"]))
                if n["active"]:
                    act = self.bg_cmd(["nmcli", "connection", "down", "id", n["ssid"]], f"Desconectado de {n['ssid']}")
                elif n["saved"]:
                    act = self.bg_cmd(["nmcli", "connection", "up", "id", n["ssid"]], f"Conectado a {n['ssid']}")
                elif n["sec"]:
                    act = (lambda ssid=n["ssid"]: core.spawn(core.TERMINAL + [
                        "--class", "rolight-ai", "--title", f"Wi-Fi {ssid}", "--hold",
                        "nmcli", "--ask", "dev", "wifi", "connect", ssid]))
                else:
                    act = self.bg_cmd(["nmcli", "dev", "wifi", "connect", n["ssid"]], f"Conectado a {n['ssid']}")
                items.append(Item(n["ssid"], sub, f"network-wireless-signal-{lvl}", act,
                                  "Conectada" if n["active"] else "Redes"))
            items += [
                Item("Buscar redes de nuevo", "", "view-refresh", self._wifi_rescan, "Opciones", close=False),
                Item("Apagar Wi-Fi", "", "network-wireless-offline",
                     self.bg_cmd(["nmcli", "radio", "wifi", "off"], "Wi-Fi apagado"), "Opciones"),
                Item("Configuración avanzada", "nm-connection-editor", "preferences-system-network",
                     lambda: core.spawn(["nm-connection-editor"]), "Opciones"),
            ]
            self.render(items, None, "↵ conectar/desconectar · redes nuevas piden clave en una terminal · ⌫ salir")
        self.async_mode("wifi", wifi_scan, show, "Buscando redes…")

    def _wifi_rescan(self):
        self.sys_cache.pop("wifi", None)
        self.async_mode("wifi", lambda: wifi_scan(True), lambda r: (self.sys_cache.__setitem__("wifi", r), self.update()),
                        "Escaneando…")
        return False

    def mode_bt(self, q):
        def show(res):
            if isinstance(res, Exception):
                return self.render([], f"Error leyendo bluetooth: {GLib.markup_escape_text(str(res))}")
            powered, devs = res
            if not powered:
                return self.render([Item("Encender Bluetooth", "", "bluetooth",
                                         self.bg_cmd(["bluetoothctl", "power", "on"], "Bluetooth encendido"))])
            ql = q.lower()
            items = []
            for d in devs:
                if ql and ql not in d["name"].lower():
                    continue
                sub = "conectado" if d["connected"] else "desconectado"
                if d["battery"]:
                    sub += f" · batería {d['battery']}%"
                cmd = "disconnect" if d["connected"] else "connect"
                msg = f"{'Desconectado' if d['connected'] else 'Conectado'}: {d['name']}"
                items.append(Item(d["name"], sub, d["icon"], self.bg_cmd(["bluetoothctl", cmd, d["mac"]], msg),
                                  "Conectados" if d["connected"] else "Dispositivos"))
            items += [
                Item("Emparejar dispositivo nuevo", "bluetuith", "bluetooth",
                     lambda: core.spawn(core.TERMINAL + ["--class", "rolight-ai", "--title", "Bluetooth",
                                                         "bluetuith"]), "Opciones"),
                Item("Apagar Bluetooth", "", "bluetooth-disabled",
                     self.bg_cmd(["bluetoothctl", "power", "off"], "Bluetooth apagado"), "Opciones"),
            ]
            self.render(items, None, "↵ conectar/desconectar · ⌫ salir")
        self.async_mode("bt", bt_devices, show, "Leyendo dispositivos…")

    def mode_sessions(self, q):
        def show(res):
            if isinstance(res, Exception):
                return self.render([], f"Error leyendo sesiones: {GLib.markup_escape_text(str(res))}")
            if not res:
                return self.render([], "No encontré sesiones de Claude Code, OpenCode ni Hermes")
            words = q.lower().split()
            items = []
            home = os.path.expanduser("~")
            for tool, title, cwd, ts, cmd in res:
                where = cwd.replace(home, "~", 1) if cwd else ""
                if words and not all(w in f"{tool} {title} {where}".lower() for w in words):
                    continue
                run = ["sh", "-c", 'cd "$0" 2>/dev/null; exec "$@"', cwd or home] + cmd
                items.append(Item(title, f"{tool}  ·  {where}  ·  {_ago(ts)}" if where else f"{tool}  ·  {_ago(ts)}",
                                  "utilities-terminal",
                                  lambda r=run, t=title: core.spawn(core.TERMINAL + ["--title", t[:60]] + r),
                                  "Recientes"))
                if len(items) >= 60:
                    break
            self.render(items, None, "↵ retomar en terminal · escribí para filtrar (claude, opencode, hermes…) · ⌫ salir")
        self.async_mode("sessions", ai_sessions, show, "Buscando sesiones…")

    def mode_vpn(self, q):
        def show(res):
            if isinstance(res, Exception):
                return self.render([], f"Error con NetworkManager: {GLib.markup_escape_text(str(res))}")
            names, active = res
            ql = q.lower()
            items = []
            card = None
            if active:
                card = f"<b>VPN conectada</b>\n<span alpha='60%'>{GLib.markup_escape_text(', '.join(active))}</span>"
                for n in active:
                    items.append(Item(f"Desconectar {n}", "nmcli", "network-vpn-disconnected",
                                      self.bg_cmd(["nmcli", "connection", "down", n], "VPN desconectada"), "Estado"))
            for name, kind in names:
                if name in active or (ql and ql not in name.lower()):
                    continue
                items.append(Item(name, kind, "network-vpn", lambda n=name: core.spawn(core.TERMINAL + [
                    "--class", "rolight-ai", "--title", f"VPN {n}", "--hold",
                    "nmcli", "--ask", "connection", "up", n]), "Perfiles"))
            items.append(Item("Configurar VPNs", "nm-connection-editor", "network-vpn",
                              lambda: core.spawn(["nm-connection-editor"]), "Opciones"))
            self.render(items, card, "↵ conectar (si pide clave, abre una terminal flotante) · ⌫ salir")
        self.async_mode("vpn", vpn_profiles, show, "Consultando VPNs…")

    def mode_monitor(self, q):
        try:
            outs = json.loads(sh(["swaymsg", "-t", "get_outputs"], 3))
        except Exception:  # noqa: BLE001
            outs = []
        connected = {o["name"] for o in outs}
        active = ", ".join(f"{o['name']} {o['rect']['width']}×{o['rect']['height']}"
                           + (f" ↻{o['transform']}" if o.get("transform") not in (None, "normal") else "")
                           for o in outs if o.get("active"))
        ql = q.lower()
        items = []
        for name, lines in kanshi_profiles():
            names = {l.split()[1].strip('"') for l in lines}
            if not names <= connected or (ql and ql not in name.lower()):
                continue
            exact = names == connected
            sub = ("coincide con lo conectado · " if exact else "") + "; ".join(
                " ".join(l.split()[1:3]) for l in lines)
            items.append(Item(name.replace("_", " "), sub, "video-display",
                              lambda l=lines, n=name: (apply_kanshi(l), core.notify("Monitores", n)),
                              "Perfiles kanshi"))
        for path in sorted(glob_scripts()):
            base = os.path.basename(path)
            if ql and ql not in base.lower():
                continue
            items.append(Item(base, "script de sway", "utilities-terminal",
                              lambda p=path: core.spawn(["sh", p]), "Scripts"))
        items.append(Item("Configurar pantallas", "nwg-displays", "preferences-desktop-display",
                          lambda: core.spawn(["nwg-displays"]), "Opciones"))
        self.render(items, f"<span alpha='60%'>Activos:</span> {GLib.markup_escape_text(active)}",
                    "↵ aplicar perfil · ⌫ salir")


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.ui = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.hold()
        act = Gio.SimpleAction.new("mode", GLib.VariantType.new("s"))
        act.connect("activate", lambda _a, v: self.open_mode(v.get_string()))
        self.add_action(act)

    def open_mode(self, mid):
        if self.ui is None:
            self.ui = Rolight(self)
        ui = self.ui
        if ui.win.get_visible() and ui.mode == mid:
            return ui.hide()
        if not ui.win.get_visible():
            ui.show()
        if mid in MODE_BY_ID:
            ui.set_mode(mid)

    start_hidden = False

    def do_activate(self):
        if self.ui is None:
            self.ui = Rolight(self)
        if self.start_hidden:  # arranque silencioso desde autostart
            self.start_hidden = False
            return
        self.ui.toggle()


# personalizaciones propias fuera del repo: local.py junto a este archivo (ver README)
_LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local.py")
if os.path.exists(_LOCAL):
    import importlib.util
    _spec = importlib.util.spec_from_file_location("rolight_local", _LOCAL)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    if hasattr(_mod, "setup"):
        _mod.setup(sys.modules[__name__])


if __name__ == "__main__":
    app = App()
    if sys.argv[1:] == ["--daemon"]:
        app.start_hidden = True
    elif len(sys.argv) > 1:  # primer arranque pidiendo un modo
        app.connect("activate", lambda a: GLib.idle_add(lambda: a.open_mode(sys.argv[1]) and False))
    sys.exit(app.run(sys.argv[:1]))
