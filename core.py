#!/usr/bin/env python3
"""Rolight para sway: modo script de rofi.

Escribí y Enter. Si lo que escribiste no coincide con nada (o usás Ctrl+Enter)
se interpreta como consulta:

  2+2*3 / =sqrt(16)     calculadora (Enter copia el resultado)
  ?pregunta / ia ...    pregunta a la IA (versión rofi) (en una terminal flotante)
  g texto               busca en la web
  f texto / /texto      busca archivos en $HOME
  ssh host / rdp host   conecta por ssh (kitty) o rdp (remmina)
  clima [ciudad]        clima actual + pronóstico
  hora [ciudad]         hora local o de otra ciudad
  www.algo.com          abre la url en el navegador predeterminado
  cualquier otra cosa   menú: web / IA / archivos
"""
import ast
import base64
import configparser
import datetime as dt
import glob
import json
import math
import operator
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo, available_timezones

# ── Ajustes ──────────────────────────────────────────────────────────
WEB_SEARCH = "https://www.google.com/search?q={}"
TERMINAL = ["kitty"]
AI_BACKEND = "opencode"                  # "opencode" o "claude"
WEATHER_CITY = ""                        # vacío = detecta por IP
FILE_SEARCH_ROOT = os.path.expanduser("~")
FIND_HIDDEN = os.environ.get("ROLIGHT_FIND_HIDDEN", "") not in ("", "0", "no")
MAX_FILES = 40
HIDE_SSH_HOSTS = {"github.com", "bitbucket.org", "ssh.dev.azure.com", "localhost"}
CACHE = os.path.expanduser("~/.cache/rolight")
HISTORY = os.path.join(CACHE, "history.json")

CITY_ALIASES = {
    "tokio": "Asia/Tokyo", "madrid": "Europe/Madrid", "londres": "Europe/London",
    "paris": "Europe/Paris", "parís": "Europe/Paris", "nueva york": "America/New_York",
    "new york": "America/New_York", "miami": "America/New_York",
    "los angeles": "America/Los_Angeles", "méxico": "America/Mexico_City",
    "mexico": "America/Mexico_City", "santiago": "America/Santiago",
    "lima": "America/Lima", "bogota": "America/Bogota", "bogotá": "America/Bogota",
    "montevideo": "America/Montevideo", "sao paulo": "America/Sao_Paulo",
    "san pablo": "America/Sao_Paulo", "buenos aires": "America/Argentina/Buenos_Aires",
    "cordoba": "America/Argentina/Cordoba", "córdoba": "America/Argentina/Cordoba",
    "berlin": "Europe/Berlin", "berlín": "Europe/Berlin", "roma": "Europe/Rome",
    "pekin": "Asia/Shanghai", "pekín": "Asia/Shanghai", "sidney": "Australia/Sydney",
    "sydney": "Australia/Sydney", "dubai": "Asia/Dubai", "utc": "UTC",
}

SEP, US = "\0", "\x1f"

# Saludo de la pantalla de inicio. Se sortea en cada apertura, dentro del grupo
# que corresponde a la hora del día. Vaciar la lista lo desactiva.
USER_NAME = "Javi"
GREETINGS = {
    "mañana": ("Buenos días", "Buen día", "Hola", "Qué onda", "Bárbaro", "Mirá vos"),
    "tarde": ("Buenas tardes", "Hola", "Qué onda", "Bárbaro", "Todo bien por acá"),
    "noche": ("Buenas noches", "Hola", "Qué onda", "Bárbaro", "¿Ronda noche?"),
}


def greeting():
    """Saludo aleatorio según la hora. '' si no hay ninguno configurado."""
    h = dt.datetime.now().hour
    grupo = "mañana" if 5 <= h < 13 else "tarde" if h < 20 else "noche"
    opciones = GREETINGS.get(grupo) or ()
    if not opciones:
        return ""
    saludo = random.choice(opciones)
    return f"{saludo}, {USER_NAME}…" if USER_NAME else f"{saludo}…"


# Texto de la caja de búsqueda, por modo. El saludo va en el home.
PLACEHOLDERS = {
    "": "",
    "clipboard": "Buscar en el portapapeles",
    "bluetooth": "Dispositivo bluetooth",
    "wifi": "Nombre de la red",
    "vpn": "VPN de nmcli, o Tailscale",
    "bitwarden": "Buscar en Bitwarden",
    "sesiones": "Buscar sesión de Claude, OpenCode o Hermes",
    "sistema": "Filtrar procesos…",
}


def placeholder_for(mode=""):
    """Texto de la caja de búsqueda para el modo dado."""
    return PLACEHOLDERS.get(mode) or greeting()


def write_theme(dest):
    """Copia rolight.rasi con el placeholder del modo.

    rofi no deja cambiar el placeholder desde script mode (`opt("placeholder",
    …)` lo pisa el tema, y `-theme-append` también), así que hay que generar el
    tema. Se reescribe la línea `placeholder:` de la entrada.
    """
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rolight.rasi")
    try:
        with open(src, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return False
    value = placeholder_for(os.environ.get("ROLIGHT_MODE", "")).replace("\\", "\\\\").replace('"', '\\"')
    text, n = re.subn(r'(?m)^(\s*)placeholder\s*:.*$',
                      lambda m: f'{m.group(1)}placeholder: "{value}";', text, count=1)
    if not n:
        return False
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        f.write(text)
    return True


# ── Salida rofi ──────────────────────────────────────────────────────
def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def opt(key, val):
    print(f"{SEP}{key}{US}{val}")


def row(title, info, icon="", sub="", meta=""):
    text = esc(title)
    if sub:
        text += f"  <span alpha='55%' size='small'>{esc(sub)}</span>"
    extras = [f"info{US}{info}"]
    if icon:
        extras.append(f"icon{US}{icon}")
    if meta:
        extras.append(f"meta{US}{meta}")
    print(text + SEP + US.join(extras))


def header(message=None, prompt="\uf002"):
    opt("prompt", prompt)
    opt("markup-rows", "true")
    if message:
        opt("message", message)


# ── Utilidades ───────────────────────────────────────────────────────
def spawn(cmd):
    """Lanza desacoplado de rofi (sin heredar su stdout)."""
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def notify(title, body=""):
    spawn(["notify-send", "-a", "Rolight", title, body])


def copy(text):
    # Wayland: wl-copy · X11 (i3, GNOME en Xorg): xclip
    cmd = ["wl-copy"] if os.environ.get("WAYLAND_DISPLAY") else ["xclip", "-selection", "clipboard"]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, start_new_session=True)
    p.communicate(text.encode())
    notify("Copiado", text)


def open_url(url):
    spawn(["xdg-open", url])


def load_history():
    try:
        with open(HISTORY) as f:
            return json.load(f)
    except Exception:
        return {}


def bump_history(key):
    h = load_history()
    h[key] = h.get(key, 0) + 1
    os.makedirs(CACHE, exist_ok=True)
    with open(HISTORY, "w") as f:
        json.dump(h, f)


# ── Aplicaciones ─────────────────────────────────────────────────────
def desktop_dirs():
    data = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
    home = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    dirs = [home] + data + ["/var/lib/flatpak/exports/share",
                            os.path.expanduser("~/.local/share/flatpak/exports/share")]
    return [os.path.join(d, "applications") for d in dirs]


def list_apps():
    apps, seen = [], set()
    for d in desktop_dirs():
        for path in sorted(glob.glob(os.path.join(d, "**/*.desktop"), recursive=True)):
            did = os.path.relpath(path, d).replace("/", "-")
            if did in seen:
                continue
            seen.add(did)
            cp = configparser.RawConfigParser(strict=False, interpolation=None)
            cp.optionxform = str
            try:
                cp.read(path, encoding="utf-8")
                e = cp["Desktop Entry"]
            except Exception:
                continue
            if e.get("Type", "Application") != "Application":
                continue
            if e.get("NoDisplay", "").lower() == "true" or e.get("Hidden", "").lower() == "true":
                continue
            only = e.get("OnlyShowIn", "")
            if only and not any(x in only for x in ("sway", "wlroots")):
                continue
            name = e.get("Name[es]") or e.get("Name")
            if not name:
                continue
            sub = e.get("GenericName[es]") or e.get("GenericName") or ""
            kw = " ".join(filter(None, [e.get("Keywords[es]"), e.get("Keywords"),
                                        e.get("Comment[es]"), e.get("Comment"),
                                        e.get("Exec", "").split(" ")[0]]))
            apps.append((name, did, path, e.get("Icon", "application-x-executable"), sub, kw))
    return apps


def launch_app(did, path):
    if shutil.which("gtk-launch"):
        spawn(["gtk-launch", did])
    else:
        spawn(["gio", "launch", path])


# ── SSH / RDP ────────────────────────────────────────────────────────
def ssh_hosts():
    hosts = []
    try:
        with open(os.path.expanduser("~/.ssh/config")) as f:
            for line in f:
                m = re.match(r"\s*Host\s+(.+)", line, re.I)
                if m:
                    for h in m.group(1).split():
                        if not any(c in h for c in "*?!") and h not in HIDE_SSH_HOSTS:
                            hosts.append(h)
    except OSError:
        pass
    return hosts


def remmina_datadir():
    """Remmina permite mover la carpeta de perfiles (datadir_path en remmina.pref)."""
    cp = configparser.RawConfigParser(strict=False, interpolation=None)
    try:
        cp.read(os.path.expanduser("~/.config/remmina/remmina.pref"))
        d = cp.get("remmina_pref", "datadir_path", fallback="").strip()
    except Exception:
        d = ""
    return d if d and os.path.isdir(d) else os.path.expanduser("~/.local/share/remmina")


def remmina_profiles():
    out = []
    for path in glob.glob(os.path.join(remmina_datadir(), "*.remmina")):
        cp = configparser.RawConfigParser(strict=False, interpolation=None)
        try:
            cp.read(path)
            r = cp["remmina"]
            out.append((r.get("name") or os.path.basename(path), r.get("protocol", ""),
                        r.get("server", ""), path, r.get("group", "")))
        except Exception:
            continue
    return sorted(out)


def run_ssh(host):
    spawn(TERMINAL + ["--title", f"ssh {host}", "ssh", host])


def run_rdp(target):
    if os.path.isfile(target):
        spawn(["remmina", "-c", target])
    else:
        spawn(["remmina", "-c", target if "://" in target else f"rdp://{target}"])


# ── Calculadora ──────────────────────────────────────────────────────
OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
       ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
       ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}
FUNCS = {k: getattr(math, k) for k in ("sqrt", "sin", "cos", "tan", "asin", "acos", "atan",
                                       "log", "log10", "log2", "exp", "floor", "ceil",
                                       "factorial", "radians", "degrees")}
FUNCS.update(abs=abs, round=round, raiz=math.sqrt, pi=math.pi, e=math.e)
MATH_RE = re.compile(r"^[\d\s.,+\-*/%^()x×÷a-z_]*$", re.I)


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in OPS:
        l, r = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(r) > 1000:
            raise ValueError("exponente muy grande")
        return OPS[type(node.op)](l, r)
    if isinstance(node, ast.UnaryOp) and type(node.op) in OPS:
        return OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Name) and isinstance(FUNCS.get(node.id), float):
        return FUNCS[node.id]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and callable(FUNCS.get(node.func.id)):
        return FUNCS[node.func.id](*[_eval(a) for a in node.args])
    raise ValueError("expresión no soportada")


def calc(text):
    """Devuelve el resultado como string, o None si no es una cuenta."""
    t = text.strip().lstrip("=").strip()
    if not t or not MATH_RE.match(t) or not re.search(r"\d", t):
        return None
    if not re.search(r"[+\-*/%^()x×÷]", t) and not re.match(r"^[a-z]+\(", t, re.I):
        return None  # un número suelto no es una cuenta
    # "15% de 200" / "20%" como porcentaje
    t = re.sub(r"(\d+(?:[.,]\d+)?)\s*%\s*de\s*", r"\1/100*", t, flags=re.I)
    t = re.sub(r"(\d)\s*%(?!\s*\d)", r"\1/100", t)
    t = t.replace("×", "*").replace("÷", "/").replace("^", "**")
    t = re.sub(r"(?<=\d)\s*x\s*(?=[\d(])", "*", t)
    t = t.replace(",", ".")
    try:
        v = _eval(ast.parse(t, mode="eval"))
    except Exception:
        return None
    if isinstance(v, float):
        if v.is_integer() and abs(v) < 1e15:
            return str(int(v))
        return f"{v:.10g}"
    return str(v)


# ── Clima / hora ─────────────────────────────────────────────────────
def weather(city):
    q = urllib.parse.quote(city or WEATHER_CITY)
    url = f"https://wttr.in/{q}?format=j1&lang=es"
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    with urllib.request.urlopen(req, timeout=6) as r:
        d = json.load(r)
    cur = d["current_condition"][0]
    area = d.get("nearest_area", [{}])[0]
    place = ", ".join(x[0]["value"] for x in (area.get("areaName"), area.get("region")) if x)
    desc = (cur.get("lang_es") or cur["weatherDesc"])[0]["value"]
    msg = (f"<b>{esc(place)}</b>\n<span size='x-large'>{cur['temp_C']}°C</span>  {esc(desc)}\n"
           f"Sensación {cur['FeelsLikeC']}°C · Humedad {cur['humidity']}% · "
           f"Viento {cur['windspeedKmph']} km/h")
    days = []
    dias = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
    for w in d["weather"][:3]:
        day = dt.date.fromisoformat(w["date"])
        h = w["hourly"][4] if len(w["hourly"]) > 4 else w["hourly"][0]
        ddesc = (h.get("lang_es") or h["weatherDesc"])[0]["value"]
        days.append((f"{dias[day.weekday()]} {day.day}", f"{w['mintempC']}° / {w['maxtempC']}°",
                     ddesc, h.get("chanceofrain", "0")))
    return msg, days


def find_tz(city):
    c = city.strip().lower()
    if not c:
        return None
    if c in CITY_ALIASES:
        return CITY_ALIASES[c]
    key = c.replace(" ", "_")
    for tz in sorted(available_timezones()):
        if tz.lower().split("/")[-1] == key:
            return tz
    for tz in sorted(available_timezones()):
        if key in tz.lower():
            return tz
    return None


def time_message(city=""):
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
             "septiembre", "octubre", "noviembre", "diciembre"]
    tzname = find_tz(city) if city else None
    if city and not tzname:
        return f"No encontré la zona horaria de «{esc(city)}»"
    now = dt.datetime.now(ZoneInfo(tzname)) if tzname else dt.datetime.now().astimezone()
    if not tzname:
        try:
            tzname = os.path.realpath("/etc/localtime").split("zoneinfo/")[1]
        except IndexError:
            tzname = now.tzname()
    lugar = tzname.split("/")[-1].replace("_", " ")
    return (f"<span size='xx-large'><b>{now:%H:%M}</b></span>  {esc(lugar)} (UTC{now:%z})\n"
            f"{dias[now.weekday()]} {now.day} de {meses[now.month - 1]} de {now.year}")


# ── Archivos ─────────────────────────────────────────────────────────
def find_files(q):
    fd = shutil.which("fd") or shutil.which("fdfind")
    if fd:
        cmd = [fd, "--ignore-case", "--max-results", str(MAX_FILES),
               "--exclude", "node_modules", "--exclude", ".git", "--", q, FILE_SEARCH_ROOT]
        # fd ignora las carpetas ocultas, así que ~/.config, ~/.ssh, etc. quedan
        # fuera. FIND_HIDDEN=1 lo activa; por default no, para no cambiar el
        # comportamiento de la versión GTK.
        if FIND_HIDDEN:
            cmd[1:1] = ["--hidden", "--exclude", ".cache"]
    else:
        cmd = ["find", FILE_SEARCH_ROOT, "-maxdepth", "6", "-not", "-path", "*/.*",
               "-iname", f"*{q}*"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=4).stdout
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
    paths = [p for p in out.splitlines() if p][:MAX_FILES]
    # primero coincidencias en el nombre, luego rutas más cortas
    paths.sort(key=lambda p: (q.lower() not in os.path.basename(p.rstrip("/")).lower(), len(p)))
    return paths


def file_icon(path):
    if os.path.isdir(path):
        return "folder"
    try:
        mime = subprocess.run(["xdg-mime", "query", "filetype", path], capture_output=True,
                              text=True, timeout=1).stdout.strip()
    except Exception:
        mime = ""
    return mime.replace("/", "-") if mime else "text-x-generic"


def show_files(q):
    paths = find_files(q)
    home = os.path.expanduser("~")
    header(f"Archivos que coinciden con «{esc(q)}»" if paths else f"Sin archivos para «{esc(q)}»")
    for p in paths:
        p = p.rstrip("/")
        parent = os.path.dirname(p).replace(home, "~", 1)
        row(os.path.basename(p), f"file:{p}", file_icon(p) if len(paths) <= 25 else "", parent)
    row(f"Buscar «{q}» en la web", f"web:{q}", "web-browser")


# ── Utilidades de red / NM ───────────────────────────────────────────
def nmcli_fields(line):
    """Divide una línea de `nmcli -t` respetando los ':' escapados (\\:)."""
    out, cur, i = [], "", 0
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line) and line[i + 1] == ":":
            cur += ":"
            i += 2
        elif c == ":":
            out.append(cur)
            cur = ""
            i += 1
        else:
            cur += c
            i += 1
    out.append(cur)
    return out


def nmcli(args, timeout=15):
    """Ejecuta nmcli y devuelve stdout, o None si no está / falla."""
    if not shutil.which("nmcli"):
        return None
    try:
        p = subprocess.run(["nmcli", "-t"] + args, capture_output=True,
                           text=True, timeout=timeout)
    except Exception:
        return None
    return p.stdout if p.returncode == 0 else None


# ── Portapapeles (copyq) ─────────────────────────────────────────────
def copyq_eval(js, timeout=6):
    if not shutil.which("copyq"):
        return None
    try:
        p = subprocess.run(["copyq", "eval", "--", js], capture_output=True,
                           text=True, timeout=timeout)
    except Exception:
        return None
    return p.stdout if p.returncode == 0 else None


def copyq_run(*args, timeout=5):
    if not shutil.which("copyq"):
        return False
    try:
        return subprocess.run(["copyq", *args], capture_output=True,
                              text=True, timeout=timeout).returncode == 0
    except Exception:
        return False


def oneline(s, n=70):
    s = re.sub(r"\s+", " ", s.replace("\n", " ")).strip()
    return s[:n] + "…" if len(s) > n else s


def show_clipboard(q=""):
    if not shutil.which("copyq"):
        header("El portapapeles necesita <b>copyq</b>")
        row("Instalar con: sudo pacman -S copyq", "hint:", "dialog-error")
        return
    # Traemos todo en una sola llamada: 200 `copyq read` suenan a ~5 s.
    raw = copyq_eval("for (let i = 0; i < count(); i++) "
                     'print(i + "\\x1e" + str(read(i)).slice(0, 2000))')
    if raw is None:
        header("El daemon de <b>copyq</b> no responde")
        row("Levantarlo", "clip-ping:", "media-playback-start", "exec copyq")
        return
    # Cada item abre con "<índice>\x1e"; los items multilínea rompen el split por líneas.
    parts = re.split(r"(\d+)\x1e", raw)
    items = {}
    for i in range(1, len(parts) - 1, 2):
        items[int(parts[i])] = parts[i + 1].rstrip("\n")

    q = q.strip().lower()
    hits = [(i, t) for i, t in items.items()
            if t.strip() and (not q or q in t.lower())]

    head = f"<b>Portapapeles</b> · {len(items)} entradas"
    if q:
        head += f" · «{esc(q)}»"
    header(head if hits else f"{head}<br>Sin coincidencias")
    for i, txt in hits[:MAX_FILES]:
        row(oneline(txt) or "(vacío)", f"clip:{i}",
            "edit-paste" if i == 0 else "", f"#{i} · {len(txt)} car.")
    if hits:
        row("Pegar en la ventana activa", f"clip-paste:{hits[0][0]}",
            "go-next", "copia y envía Ctrl+Shift+V")
    else:
        row("Volver", "home:", "go-home")


def do_clip(i, paste=False):
    # `copyq select N copy` no se usa para poner el texto: en X11 hay
    # instalaciones donde copyq lee bien pero no logra tomar la propiedad del
    # portapapeles ("Failed to copy to clipboard"). copy() usa wl-copy/xclip
    # directo, que sí funciona, y de paso copyq registra el item nuevo.
    txt = (copyq_eval(f"str(read({int(i)}))") or "")
    if not txt:
        notify("copyq no devolvió nada", f"la entrada #{i} está vacía")
        return
    copy(txt.rstrip("\n"))
    copyq_run("select", str(i))  # deja copyq apuntando a esta entrada
    if not paste:
        return
    # refocus de la ventana que tenía el foco antes de abrir rofi
    try:
        with open(os.path.join(CACHE, "active.win")) as f:
            wid = f.read().strip()
        if wid:
            spawn(["xdotool", "windowactivate", "--sync", wid])
            time.sleep(0.2)
            spawn(["xdotool", "key", "--clearmodifiers", "ctrl+shift+v"])
            return
    except Exception:
        pass
    notify("Copiado", "rofi no pudo recuperar el foco anterior")


# ── Bluetooth ────────────────────────────────────────────────────────
def bt_paired():
    out = subprocess.run(["bluetoothctl", "devices", "Paired"], capture_output=True,
                         text=True, timeout=5).stdout
    devs = []
    for line in out.splitlines():
        m = re.match(r"Device\s+([0-9A-Fa-f:]{17})\s+(.+)", line)
        if m:
            devs.append((m.group(1), m.group(2).strip()))
    return devs


def bt_connected(mac):
    try:
        out = subprocess.run(["bluetoothctl", "info", mac], capture_output=True,
                             text=True, timeout=5).stdout
    except Exception:
        return False
    m = re.search(r"Connected:\s*(yes|no)", out)
    return bool(m and m.group(1) == "yes")


def show_bluetooth(q=""):
    if not shutil.which("bluetoothctl"):
        header("El modo Bluetooth necesita <b>bluetoothctl</b>")
        row("Instalar con: sudo pacman -S bluez-utils", "hint:", "dialog-error")
        return
    q = q.strip().lower()
    devs = bt_paired()
    on = [d for d in devs if bt_connected(d[0])]
    header(f"<b>Bluetooth</b> · {len(on)} de {len(devs)} conectados")
    for mac, name in devs:
        if q and q not in name.lower():
            continue
        conn = bt_connected(mac)
        row(f"{'● ' if conn else '○ '}{name}",
            f"bt-{'disc' if conn else 'conn'}:{mac}",
            "audio-volume-high" if conn else "bluetooth",
            mac, "" if conn else "conectar")
    if not devs:
        row("Emparejá algo con bluetoothctl", "hint:", "bluetooth", "bluetoothctl")
    row("Abrir bluetuith (TUI)", "bt-tui:", "utilities-terminal", "terminal flotante")


def do_bt(mac, disconnect=False):
    verb = "disconnect" if disconnect else "connect"
    subprocess.run(["bluetoothctl", "disconnect" if disconnect else "connect", mac],
                   capture_output=True, timeout=10)
    notify("Bluetooth", f"{'desconectado' if disconnect else 'conectado'}: {mac}")


# ── Wi-Fi ────────────────────────────────────────────────────────────
def wifi_networks():
    out = nmcli(["-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list",
                 "--rescan", "yes"], timeout=20)
    if out is None:
        return None
    nets, seen = [], set()
    for line in out.splitlines():
        f = nmcli_fields(line)
        if len(f) < 4 or not f[1]:
            continue
        key = f[1]
        if key in seen:
            continue
        seen.add(key)
        nets.append((f[0] == "*", key, f[2], f[3]))
    nets.sort(key=lambda n: (not n[0], -int(n[2] or 0)))
    return nets


def show_wifi(q=""):
    if not shutil.which("nmcli"):
        header("El modo Wi-Fi necesita <b>nmcli</b>")
        return
    nets = wifi_networks()
    if nets is None:
        header("No pude hablar con NetworkManager")
        row("Reintentar", "wifi:", "view-refresh")
        row("Abrir nmtui", "wifi-tui:", "utilities-terminal")
        return
    q = q.strip().lower()
    shown = [n for n in nets if not q or q in n[1].lower()]
    on = [n for n in nets if n[0]]
    header(f"<b>Wi-Fi</b> · {len(shown)} redes" + (f" · conectada: {esc(on[0][1])}" if on else ""))
    for active, ssid, sig, sec in shown:
        row(f"{'● ' if active else ''}{ssid}", f"wifi:{urllib.parse.quote(ssid, safe='')}",
            "network-wireless-connected" if active else "network-wireless",
            f"{sig}% · {sec or 'abierta'}" + (" · activa" if active else ""))
    row("Abrir nmtui (TUI)", "wifi-tui:", "utilities-terminal", "gestión completa")


def do_wifi(ssid):
    ssid = urllib.parse.unquote(ssid)
    subprocess.run(["nmcli", "device", "wifi", "connect", ssid],
                   capture_output=True, timeout=30)
    notify("Wi-Fi", f"conectando a {ssid}…")


# ── Sesiones IA (Claude Code / OpenCode / Hermes) ────────────────────
def _claude_sessions(limit=40):
    out = []
    files = glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl"))
    for path in sorted(files, key=lambda p: os.path.getmtime(p), reverse=True)[:limit]:
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
        out.append(("Claude", title or "(sin título)",
                    json.loads(b'"' + cwd.group(1) + b'"') if cwd else "",
                    os.path.getmtime(path),
                    ["claude", "--resume", os.path.basename(path)[:-6]]))
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
                        "SELECT id, title, directory, time_updated FROM session "
                        "WHERE parent_id IS NULL AND time_archived IS NULL "
                        f"ORDER BY time_updated DESC LIMIT {limit}")
    return [("OpenCode", t or "(sin título)", d or "", u / 1000, ["opencode", "-s", i])
            for i, t, d, u in rows]


def _hermes_sessions(limit=40):
    rows = _sqlite_rows(os.path.expanduser("~/.hermes/state.db"),
                        "SELECT id, title, cwd, COALESCE(ended_at, started_at) FROM sessions "
                        "WHERE parent_session_id IS NULL AND COALESCE(archived, 0) = 0 "
                        "AND source != 'subagent' "
                        f"ORDER BY started_at DESC LIMIT {limit}")
    return [("Hermes", t or "(sin título)", d or "", u or 0, ["hermes", "--resume", i])
            for i, t, d, u in rows]


def ai_sessions():
    """(herramienta, título, carpeta, ts, cmd) de las sesiones recientes."""
    out = []
    for tool, fn in (("claude", _claude_sessions), ("opencode", _opencode_sessions),
                     ("hermes", _hermes_sessions)):
        if shutil.which(tool):
            try:
                out += fn()
            except Exception:  # noqa: BLE001
                pass
    return sorted(out, key=lambda r: r[3], reverse=True)[:150]


def ago(ts):
    if not ts:
        return ""
    d = max(0, dt.datetime.now().timestamp() - ts)
    if d < 60:
        return "ahora"
    if d < 3600:
        return f"hace {int(d // 60)} min"
    if d < 86400:
        return f"hace {int(d // 3600)} h"
    return f"hace {int(d // 86400)} d"


def show_sessions(q=""):
    header("<b>Sesiones IA</b> · buscando…")
    res = ai_sessions()
    if not res:
        header("No encontré sesiones de Claude Code, OpenCode ni Hermes")
        row("Volver", "home:", "go-home")
        return
    words = q.lower().split()
    home = os.path.expanduser("~")
    icons = {"Claude": "dialog-information", "OpenCode": "utilities-terminal",
             "Hermes": "emblem-documents"}
    shown = 0
    for tool, title, cwd, ts, cmd in res:
        where = cwd.replace(home, "~", 1) if cwd else ""
        if words and not all(w in f"{tool} {title} {where}".lower() for w in words):
            continue
        # `cd` antes de exec: varias herramientas guardan la ruta en la sesión.
        run = ["sh", "-c", 'cd "$0" 2>/dev/null; exec "$@"', cwd or home] + cmd
        import base64
        payload = base64.b64encode(json.dumps(run).encode()).decode()
        row(oneline(title, 52), f"ses:{payload}", icons.get(tool, "utilities-terminal"),
            f"{tool} · {where or '~'} · {ago(ts)}" if where else f"{tool} · {ago(ts)}")
        shown += 1
        if shown >= 60:
            break
    if not shown:
        header(f"Sin sesiones que coincidan con «{esc(q)}»")
        row("Volver", "sessions-home:", "go-back")


def do_session(payload):
    try:
        run = json.loads(base64.b64decode(payload).decode())
    except Exception:
        notify("Sesión inválida", payload[:40])
        return
    spawn(TERMINAL + ["--class", "rolight-ai", "--title", run[-1][:50]] + run)


# ── Sistema (CPU / RAM / disco / temperaturas / procesos) ─────────────
def _read(path, default=""):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return default


def _gb(b):
    return f"{b / 1024 ** 3:.1f} GB"


def _bar(pct, width=18):
    pct = max(0, min(100, pct))
    full = round(pct / 100 * width)
    color = "#75AD47" if pct < 60 else "#D09214" if pct < 85 else "#F8747E"
    return (f"<span font_family='JetBrainsMono Nerd Font Mono' foreground='{color}'>"
            f"{'█' * full}</span>"
            f"<span font_family='JetBrainsMono Nerd Font Mono' alpha='25%'>"
            f"{'█' * (width - full)}</span>")


def _tcolor(t):
    return "#75AD47" if t < 65 else "#D09214" if t < 85 else "#F8747E"


def cpu_now():
    """% de uso en el último intervalo, leyendo /proc/stat dos veces."""
    def snap():
        for line in _read("/proc/stat").splitlines():
            if line.startswith("cpu "):
                f = [int(x) for x in line.split()[1:]]
                idle = f[3] + (f[4] if len(f) > 4 else 0)
                return sum(f), idle
        return 0, 0
    t1, i1 = snap()
    time.sleep(0.12)
    t2, i2 = snap()
    dt = t2 - t1
    return 100 * (1 - (i2 - i1) / dt) if dt else 0.0


def sensors():
    """Temps y ventiladores, igual que sensors(1).

    Se parsea la salida humana (no `sensors -u`) porque `-u` mete sufijos
    `_input`/`_max` en cada label y ensucia el mapeo. Formato típico:
        coretemp-isa-0000
        Adapter: ISA adapter
        Package id 0:  +63.0°C  (high = +84.0°C, crit = +100.0°C)
    """
    try:
        out = subprocess.run(["sensors"], capture_output=True, text=True, timeout=4)
    except Exception:
        return {}, []
    if out.returncode != 0:
        return {}, []
    temps, fans, chip = {}, [], None
    for line in out.stdout.splitlines():
        # Cabecera de chip: "coretemp-isa-0000", sin ':'. Las líneas "Adapter: …"
        # sí lo tienen y hay que descartarlas.
        if line.strip() and ":" not in line:
            chip = line.strip()
            temps.setdefault(chip, {})
            continue
        m = re.match(r"^\s*([\w][\w ]*?):\s+\+?([\d.]+)°C", line)
        if m and chip:
            temps[chip][m.group(1)] = float(m.group(2))
            continue
        m = re.match(r"^\s*(fan\d+):\s+([\d.]+)", line)
        if m:
            v = float(m.group(2))
            fans.append(v)
            # amdgpu reporta fan1/fan2 en rpm en vez de °C.
            temps.setdefault(m.group(1), {})["rpm"] = v
    return temps, fans


def show_system(q=""):
    q = q.strip().lower()
    if q.startswith("p "):
        return show_procs(q[2:])

    try:
        import psutil  # noqa: F401
        have_psutil = True
    except ImportError:
        have_psutil = False

    if have_psutil:
        import psutil
        cpu = psutil.cpu_percent(interval=0.15)
        vm, sw, du = psutil.virtual_memory(), psutil.swap_memory(), psutil.disk_usage("/")
        up = int(dt.datetime.now().timestamp() - psutil.boot_time())
    else:
        mi = dict(re.findall(r"(\w+):\s+(\d+)", _read("/proc/meminfo")))
        cpu = cpu_now()
        total = int(mi.get("MemTotal", 1)) * 1024
        used = total - int(mi.get("MemAvailable", 0)) * 1024
        st = os.statvfs("/")
        dtot = st.f_blocks * st.f_frsize
        dud = (st.f_blocks - st.f_bfree) * st.f_frsize
        vm = type("V", (), {"total": total, "used": used})()
        sw = type("V", (), {"total": int(mi.get("SwapTotal", 0)) * 1024,
                            "used": (int(mi.get("SwapTotal", 0)) - int(mi.get("SwapFree", 0))) * 1024})()
        du = type("V", (), {"total": dtot, "used": dud})()
        up = float(_read("/proc/uptime", "0").split()[0])

    lbl = lambda t: f"<span font_family='JetBrainsMono Nerd Font Mono' alpha='60%'>{t:<6}</span>"  # noqa: E731
    lines = [f"{lbl('CPU')} {_bar(cpu)}  <b>{cpu:4.0f}%</b>  "
             f"<span alpha='60%'>carga {os.getloadavg()[0]:.2f}</span>"]
    mp = 100 * vm.used / vm.total
    lines.append(f"{lbl('RAM')} {_bar(mp)}  <b>{mp:4.0f}%</b>  "
                 f"<span alpha='60%'>{_gb(vm.used)} de {_gb(vm.total)}</span>")
    if getattr(sw, "total", 0):
        sp = 100 * sw.used / sw.total
        lines.append(f"{lbl('Swap')} {_bar(sp)}  <b>{sp:4.0f}%</b>  "
                     f"<span alpha='60%'>{_gb(sw.used)} de {_gb(sw.total)}</span>")
    dp = 100 * du.used / du.total
    aviso = (" · <b><span foreground='#F8747E'>poco espacio</span></b>" if dp >= 90 else "")
    lines.append(f"{lbl('Disco')} {_bar(dp)}  <b>{dp:4.0f}%</b>  "
                 f"<span alpha='60%'>{_gb(du.total - du.used)} libres</span>{aviso}")

    t, fans = sensors()
    # sensors(1) nombra los chips con sufijo de bus ("coretemp-isa-0000",
    # "nvme-pci-0400"), así que se busca por prefijo.
    def find(prefix, *labels):
        for chip in sorted(t):
            if chip.startswith(prefix):
                c = t[chip]
                for lab in labels:
                    if lab in c:
                        return c[lab]
                vals = [v for k, v in c.items() if isinstance(v, (int, float))]
                return max(vals) if vals else None
        return None

    parts = []
    seen = set()
    for prefix, label, labels in (
        ("coretemp", "Micro", ("Package id 0",)),
        ("k10temp", "CPU", ("Tctl", "Tdie")),
        ("zenpower", "CPU", ("Tdie",)),
        ("nct6775", "Main", ("CPU", "temp1")),
        ("amdgpu", "GPU", ("edge", "junction")),
        ("nouveau", "GPU", ("GPU",)),
        ("thinkpad", "GPU", ("GPU",)),
        ("nvme", "SSD", ("Composite",)),
        ("iwlwifi", "Wi-Fi", ("temp1",)),
        ("acpitz", "Notebook", ("temp1",)),
    ):
        if label in seen:
            continue
        val = find(prefix, *labels)
        if val is not None:
            seen.add(label)
            parts.append(f"{label} <b><span foreground='{_tcolor(val)}'>{val:.0f}°</span></b>")
    if parts:
        lines.append(f"{lbl('Temp')} " + "  ·  ".join(parts))
    if fans:
        lines.append(f"{lbl('Fan')} <span alpha='60%'>"
                     + " / ".join(f"{v:.0f} rpm" for v in fans) + "</span>")
    lines.append(f"{lbl('Up')} <span alpha='60%'>{up // 3600}h {up % 3600 // 60}m · "
                 f"{len(os.sched_getaffinity(0))} núcleos</span>")

    header("<b>Sistema</b><br>" + "<br>".join(lines))
    if dp >= 90:
        row("Ver qué ocupa el disco (ncdu)", "sys-ncdu:", "drive-harddisk",
            "borrar espacio", "disco lleno espacio ncdu du")
    if have_psutil:
        row("Ver procesos (psutil)", "sys-procs:", "utilities-system-monitor",
            "filtrá con: i p <nombre>", "procesos process ps top")
    else:
        row("Instalar psutil para el detalle de procesos", "sys-psutil:", "utilities-terminal",
            "pip install --user psutil", "psutil procesos")
    row("Abrir btop", "sys-btop:", "utilities-system-monitor", "TUI completo")
    row("Volver", "home:", "go-home")


def show_procs(q=""):
    try:
        import psutil
    except ImportError:
        show_system("")
        return
    # cpu_percent necesita una llamada previa que fije la referencia: la primera
    # siempre devuelve 0, así que se mide un intervalo real.
    for p in psutil.process_iter(["pid"]):
        try:
            p.cpu_percent(None)
        except Exception:  # noqa: BLE001
            pass
    time.sleep(0.25)
    procs = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
        try:
            i = p.info
            procs.append((i["cpu_percent"] or 0, i["memory_percent"] or 0,
                          i["name"] or "?", i["pid"]))
        except Exception:  # noqa: BLE001
            continue
    procs.sort(reverse=True)
    procs = [p for p in procs if p[0] or p[1]][:40]
    if q:
        procs = [p for p in procs if q in p[2].lower() or q in str(p[3])]
    header(f"<b>Procesos</b> · {'filtrando «' + esc(q) + '»' if q else 'los más activos'}")
    if not procs:
        row("Sin coincidencias", "sys-procs:", "go-back")
        return
    for cpu, mem, name, pid in procs:
        row(f"{name}", f"sys-proc:{pid}", "", f"pid {pid} · CPU {cpu:.1f}% · RAM {mem:.1f}%")
    row("Volver", "sys:", "go-back")


# ── Tailscale ────────────────────────────────────────────────────────
def tailscale_json(timeout=4):
    if not shutil.which("tailscale"):
        return None
    try:
        p = subprocess.run(["tailscale", "status", "--json"], capture_output=True,
                           text=True, timeout=timeout)
    except Exception:
        return None
    if p.returncode != 0:
        return None
    try:
        return json.loads(p.stdout or "{}")
    except Exception:
        return None


def tailscale_up(timeout=20):
    """Levanta Tailscale. up/down/switch escriben en el socket de root, así que
    si no estamos en sudo se reintenta con sudo -n (para no quedar esperando una
    contraseña dentro de un menú)."""
    if subprocess.run(["tailscale", "up"], capture_output=True,
                      timeout=timeout).returncode == 0:
        return True
    try:
        return subprocess.run(["sudo", "-n", "tailscale", "up"], capture_output=True,
                              timeout=timeout).returncode == 0
    except Exception:
        return False


def tailscale_down(timeout=10):
    if subprocess.run(["tailscale", "down"], capture_output=True,
                      timeout=timeout).returncode == 0:
        return True
    try:
        return subprocess.run(["sudo", "-n", "tailscale", "down"], capture_output=True,
                              timeout=timeout).returncode == 0
    except Exception:
        return False


def tailscale_ago(segundos):
    """'hace 3 min' / 'ahora' a partir de un timestamp epoch."""
    d = max(0, int(dt.datetime.now().timestamp() - segundos))
    if d < 60:
        return "ahora"
    if d < 3600:
        return f"hace {d // 60} min"
    if d < 86400:
        return f"hace {d // 3600} h"
    return f"hace {d // 86400} d"


def show_tailscale(q=""):
    d = tailscale_json()
    if d is None:
        header("No pude hablar con <b>tailscaled</b>")
        row("¿Está el servicio activo?", "hint:", "dialog-error",
            "systemctl status tailscaled")
        row("Volver", "vpn:", "go-back")
        return

    state = d.get("BackendState") or "Unknown"
    ips = d.get("TailscaleIPs") or []
    # BackendState puede quedar "Stopped" mientras el túnel sigue de pie; lo que
    # manda es tener IP de tailnet y peers, no el estado del backend.
    running = state == "Running" or bool(ips)
    state = state if state == "Running" else state.title()
    host = ((d.get("Self") or {}).get("HostName") or
            (d.get("Self") or {}).get("DNSName", "").rstrip(".")) or "este equipo"
    net = d.get("MagicDNSSuffix") or ""

    head = (f"<b>Tailscale</b> · {'conectado' if running else state.lower()}"
            f"<br>{esc(host)} · {esc(ips[0] if ips else 'sin IP')}"
            + (f" · <span alpha='60%'>{esc(net)}</span>" if net else ""))
    q = q.strip().lower()

    if running:
        row("Desconectar Tailscale", "ts-down:", "network-offline",
            "toca todo el tráfico de tailnet", "tailscale down desconectar salir")
    else:
        row("Conectar Tailscale", "ts-up:", "network-vpn", "tailscale up", "tailscale up conectar")

    peers = []
    for key, peer in (d.get("Peer") or {}).items():
        name = (peer.get("HostName") or peer.get("DNSName", "").rstrip(".") or
                peer.get("TailscaleIPs", ["?"])[0])
        os_name = (peer.get("OS") or "").lower()
        icon = {"linux": "computer", "windows": "computer", "darwin": "computer",
                "ios": "phone", "android": "phone"}.get(os_name, "network-server")
        last = peer.get("LastSeen") or ""
        ago = ""
        if last and last.endswith("Z") and not last.startswith("0001"):
            try:
                ago = tailscale_ago(dt.datetime.fromisoformat(
                    last.replace("Z", "+00:00")).timestamp())
            except ValueError:
                ago = ""
        online = bool(peer.get("Online"))
        if q and q not in name.lower():
            continue
        peers.append((not online, name, icon,
                      "en línea" if online else (ago or "offline"),
                      peer.get("TailscaleIPs", [""])[0]))
    peers.sort()

    if peers:
        header(head + f"<br><span alpha='60%'>{len(peers)} equipos en el tailnet</span>")
        for _, name, icon, status, ip in peers:
            row(name, f"ts-peer:{ip or name}", icon, status)
    else:
        header(head)
        row("Sin equipos que coincidan" if q else "Tailnet vacío", "vpn:", "go-back")
    row("Abrir VPN (nmcli) para WireGuard/OpenVPN", "vpn:", "network-vpn")


def do_ts_peer(ip):
    """Abre el peer en el navegador de TTS: la URL sirve para ping y para
    abrir la consola de un nodo propio."""
    if not ip:
        return
    web = os.environ.get("ROLIGHT_TAILSCALE_WEB", "https://tailscale.com")
    open_url(f"{web}/ping?target={urllib.parse.quote(ip)}")


# ── VPN ──────────────────────────────────────────────────────────────
def vpn_conns():
    out = nmcli(["-f", "NAME,TYPE,STATE", "connection", "show"])
    if out is None:
        return None
    res = []
    for line in out.splitlines():
        f = nmcli_fields(line)
        if len(f) >= 3 and f[1] in ("vpn", "wireguard", "openvpn"):
            res.append((f[0], f[1], f[2]))
    return res


def show_vpn(q=""):
    # Tailscale primero: no aparece como perfil en nmcli (es un dispositivo tun),
    # así que sin esto el tailnet entero es invisible desde rolight.
    if tailscale_json() is not None:
        return show_tailscale(q)
    if not shutil.which("nmcli"):
        header("El modo VPN necesita <b>nmcli</b>")
        return
    conns = vpn_conns()
    if conns is None:
        header("No pude hablar con NetworkManager")
        row("Reintentar", "vpn:", "view-refresh")
        return
    q = q.strip().lower()
    shown = [c for c in conns if not q or q in c[0].lower()]
    active = [c for c in conns if c[2] == "activated"]
    header(f"<b>VPN</b> · {len(conns)} perfiles"
           + (f" · {esc(active[0][0])} activa" if active else ""))
    if not conns:
        row("No hay perfiles VPN en NetworkManager", "hint:", "network-vpn")
    for name, typ, state in shown:
        up = state == "activated"
        row(f"{'● ' if up else '○ '}{name}",
            f"vpn-{'down' if up else 'up'}:{urllib.parse.quote(name, safe='')}",
            "network-vpn" if up else "network-offline",
            f"{typ} · {state}")
    if not shown and conns:
        row("Volver", "vpn:", "go-home")


def do_vpn(name, up):
    name = urllib.parse.unquote(name)
    subprocess.run(["nmcli", "connection", "up" if up else "down", name],
                   capture_output=True, timeout=30)
    notify("VPN", f"{'conectando' if up else 'desconectando'}: {name}")


# ── Bitwarden (bw) ───────────────────────────────────────────────────
def bw_items(q):
    """Busca items con `bw`. Devuelve (items, error)."""
    bw = shutil.which("bw") or shutil.which("rbw")
    if not bw:
        return None, "Necesitás <b>bw</b> (o rbw) en el PATH"
    if bw.endswith("rbw"):
        try:
            p = subprocess.run(["rbw", "list", "items"], capture_output=True,
                               text=True, timeout=20)
        except Exception:
            return None, "rbw falló"
        if p.returncode != 0:
            return None, "No pude leer la bóveda (¿desbloqueada?)"
        try:
            items = json.loads(p.stdout or "[]")
        except Exception:
            return None, "respuesta inválida de rbw"
    else:
        try:
            p = subprocess.run(["bw", "sync"], capture_output=True, text=True, timeout=30)
            if p.returncode != 0:
                err = oneline(p.stderr or p.stdout, 160)
                hint = ("<br>Desbloqueá con <b>bw unlock</b> en una terminal."
                        if "locked" in err.lower() else "")
                return None, f"<b>bw</b>: {esc(err)}{hint}"
            p = subprocess.run(["bw", "list", "items", "--search", q],
                               capture_output=True, text=True, timeout=20)
        except subprocess.TimeoutExpired:
            return None, "bw tardó demasiado (¿sin red?)"
        except Exception:
            return None, "bw falló"
        if p.returncode != 0:
            return None, f"bw: {oneline(p.stderr or p.stdout, 120)}"
        try:
            items = json.loads(p.stdout or "[]")
        except Exception:
            return None, "respuesta inválida de bw"
    if q:
        ql = q.lower()
        items = [i for i in items if ql in (i.get("name") or "").lower()
                 or ql in (i.get("login", {}) or {}).get("username", "").lower()]
    return items, None


def show_bw(q=""):
    q = q.strip()
    items, err = bw_items(q)
    if err:
        header(err)
        unlock = "locked" in err.lower()
        row("Abrir terminal para " + ("desbloquear" if unlock else "iniciar sesión"),
            f"bw-login:{'unlock' if unlock else 'login'}", "utilities-terminal",
            "bw unlock" if unlock else "bw login")
        return
    head = "<b>Bitwarden</b>" + (f" · «{esc(q)}»" if q else "")
    header(head if items else f"{head}<br>Sin resultados")
    for it in items[:MAX_FILES]:
        login = (it.get("login") or {})
        user = login.get("username") or ""
        row(it.get("name") or "(sin nombre)", f"bw:{it.get('id')}", "dialog-password",
            user, (user + " " + (it.get("notes") or "")).strip())
    if not items:
        row("Volver", "home:", "go-home")


def do_bw(item_id):
    p = subprocess.run(["bw", "get", "password", item_id],
                       capture_output=True, text=True, timeout=15)
    if p.returncode != 0 or not p.stdout.strip():
        notify("Bitwarden", "no pude leer la contraseña")
        return
    copy(p.stdout.strip())


# ── Pantallas ────────────────────────────────────────────────────────
POWER = [
    ("Bloquear pantalla", "lock", "system-lock-screen", "lock bloquear"),
    ("Suspender", "suspend", "system-suspend", "suspend sleep dormir"),
    ("Cerrar sesión", "logout", "system-log-out", "logoff logout salir exit"),
    ("Reiniciar", "reboot", "system-reboot", "reboot restart reinicio"),
    ("Apagar", "poweroff", "system-shutdown", "shutdown poweroff apagar"),
]


# Vistas rápidas de la pantalla de inicio: (rótulo, info, ícono, subtítulo,
# palabras clave). Las palabras clave son las que matchea el filtro de rofi, así
# que van amplia: "cpu" tiene que encontrar el sistema, no solo cpu-x.
HOME_VIEWS = (
    ("Sistema", "sys:", "utilities-system-monitor", "cpu ram disco · i",
     "cpu procesador ram memoria disco temperatura calor proceso sistema stats "
     "bateria swap"),
    ("Portapapeles", "clip-home:", "edit-paste", "historial · c",
     "clipboard copiar pegar portapapeles clipboard"),
    ("Sesiones IA", "sessions-home:", "dialog-information", "retomar · e",
     "sesion claude opencode hermes charla sesion ia continuar"),
    ("Wi-Fi", "wifi-home:", "network-wireless", "redes · w",
     "wifi wi-fi inalambrico red internet"),
    ("VPN", "vpn-home:", "network-vpn", "perfiles · v",
     "vpn wireguard openvpn tailscale tailnet"),
    ("Bluetooth", "bt-home:", "bluetooth", "conectar · b",
     "bluetooth bt auricular headphone"),
    ("Bitwarden", "bw-home:", "dialog-password", "bóveda · k",
     "bitwarden clave password contrasena bovedа"),
)


# Palabras que muestran los datos del sistema directo, sin lista ni Enter: el
# mismo criterio que STAT_WORDS en la versión GTK. Se chequean mientras se
# escribe (rofi vuelve a llamar al script en cada tecla con el texto en argv[1]).
STAT_WORDS = ("cpu", "mem", "memoria", "ram", "temp", "temperatura", "disco", "disk",
              "bateria", "batería", "battery", "sistema", "stats", "procesos", "proc",
              "ventilador", "fan", "swap", "carga")


def show_home():
    header()  # el saludo va en el placeholder, no acá (salía duplicado)
    # Las vistas de herramientas van PRIMERO. Si fueran al final, escribir "cpu"
    # las dejaba debajo de cpu-x y de las demás apps que matchean, y con 8
    # líneas visibles nunca llegabas a verlas.
    for label, info, icon, sub, kw in HOME_VIEWS:
        row(label, info, icon, sub, kw)
    hist = load_history()
    apps = list_apps()
    apps.sort(key=lambda a: (-hist.get("app:" + a[1], 0), a[0].lower()))
    for name, did, path, icon, sub, kw in apps:
        row(name, f"app:{did}|{path}", icon, sub, kw)
    row("Clima", "weather:", "weather-few-clouds", "pronóstico actual", "clima weather tiempo")
    row("Hora", "time:", "preferences-system-time", "fecha y hora", "hora reloj time fecha")
    row("Calculadora", "hint:calc", "accessories-calculator", "escribí una cuenta, ej. 12*3+4",
        "calc calculadora sumar restar")
    for h in ssh_hosts():
        row(f"SSH {h}", f"ssh:{h}", "utilities-terminal", "conectar por ssh", "ssh servidor")
    for name, proto, server, path, _group in remmina_profiles():
        row(name, f"rdp:{path}", "org.remmina.Remmina", f"{proto} {server}", "remmina rdp vnc")
    row("Remmina", "app-remmina", "org.remmina.Remmina", "nueva conexión remota",
        "rdp vnc escritorio remoto")
    for title, act, icon, kw in POWER:
        row(title, f"power:{act}", icon, "sistema", kw)


def show_apps_like(q, limit=8):
    """Filas de las apps que matchean la búsqueda, para el fallback de texto libre.

    Sin esto, escribir "firefox" caía en el menú "¿Qué hago con…?" y no había
    forma de llegar a una app por el nombre sin pasar por la lista vacía.
    """
    q = q.strip().lower()
    if not q:
        return []
    hist = load_history()
    hits = []
    for name, did, path, icon, sub, kw in list_apps():
        if q in name.lower() or q in (kw or "").lower():
            hits.append((-hist.get("app:" + did, 0), name.lower(), name, did, path, icon, sub))
    hits.sort()
    return hits[:limit]


def show_query(t):
    """Texto libre: lo interpretamos."""
    low = t.lower()
    m = re.match(r"^(\?|ia |ai |claude )\s*(.+)", t, re.I)
    if m:
        return ask_ai(m.group(2))
    m = re.match(r"^(g|web|google)\s+(.+)", t, re.I)
    if m:
        return open_url(WEB_SEARCH.format(urllib.parse.quote_plus(m.group(2))))
    m = re.match(r"^(?:f\s+|/)(.+)", t)
    if m:
        return show_files(m.group(1).strip())
    m = re.match(r"^ssh\s+(\S+)$", t, re.I)
    if m:
        return run_ssh(m.group(1))
    m = re.match(r"^(rdp|remmina|vnc)\s+(\S+)$", t, re.I)
    if m:
        proto = "vnc" if m.group(1).lower() == "vnc" else "rdp"
        target = m.group(2) if "://" in m.group(2) else f"{proto}://{m.group(2)}"
        return run_rdp(target)
    m = re.match(r"^(clima|weather|tiempo)\s*(.*)$", low)
    if m:
        return show_weather(m.group(2))
    m = re.match(r"^(hora|time)\s*(?:en\s+)?(.*)$", low)
    if m:
        msg = time_message(m.group(2))
        header(msg)
        hhmm = re.search(r"\d\d:\d\d", msg)
        if hhmm:
            row(f"Copiar {hhmm.group()}", f"copy:{hhmm.group()}", "edit-copy")
        return
    # ── modos de herramientas: letra + espacio (o la letra sola) ──
    m = re.match(r"^(c|clip|clipboard|portapapeles)(?:\s+(.*))?$", low)
    if m:
        return show_clipboard(m.group(2) or "")
    m = re.match(r"^(b|bt|bluetooth)(?:\s+(.*))?$", low)
    if m:
        return show_bluetooth(m.group(2) or "")
    m = re.match(r"^(w|wifi|wi-fi)(?:\s+(.*))?$", low)
    if m:
        return show_wifi(m.group(2) or "")
    m = re.match(r"^(v|vpn)(?:\s+(.*))?$", low)
    if m:
        return show_vpn(m.group(2) or "")
    m = re.match(r"^(k|kb|bw|bitwarden)(?:\s+(.*))?$", low)
    if m:
        return show_bw(m.group(2) or "")
    m = re.match(r"^(e|ses|sesion|sessions|ai-ses)(?:\s+(.*))?$", low)
    if m:
        return show_sessions(m.group(2) or "")
    m = re.match(r"^(i|sys|sistema|stats|proc|procesos)(?:\s+(.*))?$", low)
    if m:
        return show_system(m.group(2) or "")
    if re.match(r"^(https?://|www\.)\S+$|^[\w-]+(\.[\w-]+)+(/\S*)?$", t) and not calc(t):
        url = t if t.startswith("http") else "https://" + t
        return open_url(url)
    if low in ("reboot", "reiniciar", "shutdown", "apagar", "poweroff", "logoff",
               "logout", "cerrar sesion", "cerrar sesión", "suspender", "bloquear", "lock"):
        act = {"reboot": "reboot", "reiniciar": "reboot", "shutdown": "poweroff",
               "apagar": "poweroff", "poweroff": "poweroff", "suspender": "suspend",
               "bloquear": "lock", "lock": "lock"}.get(low, "logout")
        return confirm_power(act)

    res = calc(t)
    if res is not None:
        header(f"<span alpha='60%'>{esc(t)} =</span>\n<span size='xx-large'><b>{esc(res)}</b></span>")
        row(f"= {res}", f"copy:{res}", "accessories-calculator", "Enter copia el resultado")
        row(f"Preguntar a la IA: {t}", f"ai:{t}", "help-about")
        return
    apps = show_apps_like(t)
    header(f"¿Qué hago con «{esc(t)}»?" if not apps else None)
    for _, _, name, did, path, icon, sub in apps:
        row(name, f"app:{did}|{path}", icon, sub)
    row(f"Buscar en la web: {t}", f"web:{t}", "web-browser")
    row(f"Preguntar a la IA: {t}", f"ai:{t}", "help-about")
    row(f"Buscar archivos: {t}", f"files:{t}", "system-file-manager")


def show_weather(city):
    try:
        msg, days = weather(city.strip())
    except Exception as e:
        header(f"No pude obtener el clima ({esc(type(e).__name__)})")
        row("Reintentar", f"weather:{city}", "view-refresh")
        return
    header(msg)
    for day, temps, desc, rain in days:
        row(f"{day}   {temps}", f"wttr:{city}", "weather-few-clouds", f"{desc} · lluvia {rain}%")
    row("Pronóstico completo en terminal", f"wttr:{city}", "utilities-terminal")


def confirm_power(act):
    title = next(p[0] for p in POWER if p[1] == act)
    header(f"¿Seguro que querés <b>{esc(title.lower())}</b>?")
    row(f"Sí, {title.lower()}", f"dopower:{act}", next(p[2] for p in POWER if p[1] == act))
    row("Cancelar", "home:", "dialog-cancel")


def logout_cmd():
    """Cerrar sesión según el WM en uso."""
    if os.environ.get("SWAYSOCK"):
        return ["swaymsg", "exit"]
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return ["hyprctl", "dispatch", "exit"]
    if os.environ.get("I3SOCK") or shutil.which("i3-msg") and os.environ.get("DESKTOP_SESSION") == "i3":
        return ["i3-msg", "exit"]
    return ["loginctl", "terminate-session", os.environ.get("XDG_SESSION_ID", "")]


def lock_cmd():
    """Comando de bloqueo de pantalla para el WM actual.

    Wayland → swaylock. X11 (i3/bspwm) → el script de lock del WM si existe
    (suele aplicar blur al fondo), o i3lock / xsecurelock.
    Se puede forzar con ROLIGHT_LOCK_CMD. Devuelve None si no hay ninguno.
    """
    override = os.environ.get("ROLIGHT_LOCK_CMD")
    if override:
        return shlex.split(override)
    wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
    if not wayland:
        xdg = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
        for script in (os.path.join(xdg, "i3/scripts/lock.sh"),
                       os.path.join(xdg, "bspwm/scripts/lock.sh")):
            if os.access(script, os.X_OK):
                return [script]
    # Lockers nativos de cada lado: i3lock es de X11 y no sirve en Wayland.
    cands = (["swaylock", "-f", "-c", "000000"],) if wayland else (["i3lock"], ["xsecurelock"])
    for cand in cands:
        if shutil.which(cand[0]):
            return cand
    return None


def do_power(act):
    if act == "lock":
        cmd = lock_cmd()
        if not cmd:
            notify("Sin locker de pantalla", "instalá i3lock, swaylock o xsecurelock")
            return
        spawn(cmd)
        return
    cmds = {
        "suspend": ["systemctl", "suspend"],
        "logout": logout_cmd(),
        "reboot": ["systemctl", "reboot"],
        "poweroff": ["systemctl", "poweroff"],
    }
    spawn(cmds[act])


def ai_name():
    return "OpenCode" if AI_BACKEND == "opencode" else "Claude"


def ai_terminal_cmd(q="", session=None):
    """Comando para seguir la charla con la IA en una terminal."""
    if AI_BACKEND == "opencode":
        return ["opencode"] + (["-s", session] if session else []) + (["--prompt", q] if q and not session else [])
    return ["claude"] + (["--resume", session] if session else []) + ([q] if q and not session else [])


def ask_ai(q, session=None):
    spawn(TERMINAL + ["--class", "rolight-ai", "--title", "Rolight · IA"] + ai_terminal_cmd(q, session))


def handle_info(info, text):
    kind, _, val = info.partition(":")
    if kind == "app":
        did, _, path = val.partition("|")
        bump_history("app:" + did)
        launch_app(did, path)
    elif kind == "app-remmina":
        spawn(["remmina"])
    elif kind == "power":
        confirm_power(val) if val in ("reboot", "poweroff", "logout") else do_power(val)
    elif kind == "dopower":
        do_power(val)
    elif kind == "home":
        show_home()
    elif kind == "ssh":
        run_ssh(val)
    elif kind == "rdp":
        run_rdp(val)
    elif kind == "weather":
        show_weather(val)
    elif kind == "wttr":
        loc = urllib.parse.quote(val or WEATHER_CITY)
        spawn(TERMINAL + ["--class", "rolight-ai", "--title", "Clima", "--hold",
                          "curl", "-s", f"https://wttr.in/{loc}?lang=es"])
    elif kind == "time":
        header(time_message())
    elif kind == "hint":
        header("Escribí la cuenta y Enter: <b>12*3+4</b>, <b>2^10</b>, <b>15% de 200</b>, <b>sqrt(81)</b>")
    elif kind == "copy":
        copy(val)
    elif kind == "web":
        open_url(WEB_SEARCH.format(urllib.parse.quote_plus(val)))
    elif kind == "ai":
        ask_ai(val)
    elif kind == "files":
        show_files(val)
    elif kind == "file":
        spawn(["xdg-open", val])
    elif kind == "clip":
        do_clip(int(val) if val.isdigit() else 0)
    elif kind == "clip-paste":
        do_clip(int(val) if val.isdigit() else 0, paste=True)
    elif kind == "clip-ping":
        spawn(["copyq"])
    elif kind == "bt-conn":
        do_bt(val, disconnect=False)
    elif kind == "bt-disc":
        do_bt(val, disconnect=True)
    elif kind == "bt-tui":
        spawn(TERMINAL + ["--class", "rolight-ai", "--title", "bluetuith",
                          "bluetuith"])
    elif kind == "wifi":
        do_wifi(val)
    elif kind == "wifi-tui":
        spawn(TERMINAL + ["--class", "rolight-ai", "--title", "nmtui", "nmtui"])
    elif kind == "vpn-up":
        do_vpn(val, up=True)
    elif kind == "vpn-down":
        do_vpn(val, up=False)
    elif kind == "ts-up":
        notify("Tailscale", "conectando…" if tailscale_up() else "no pude conectar")
    elif kind == "ts-down":
        notify("Tailscale", "desconectado" if tailscale_down() else "no pude desconectar")
    elif kind == "ts-peer":
        do_ts_peer(val)
    elif kind == "bw":
        do_bw(val)
    elif kind == "bw-unlock":
        spawn(TERMINAL + ["--class", "rolight-ai", "--title", "Bitwarden",
                          "bw", "unlock"])
    elif kind == "bw-login":
        spawn(TERMINAL + ["--class", "rolight-ai", "--title", "Bitwarden",
                          "bw", val])
    elif kind == "ses":
        do_session(val)
    elif kind == "sessions-home":
        show_sessions("")
    elif kind == "sys":
        show_system("")
    elif kind == "sys-procs":
        show_procs("")
    elif kind == "sys-proc":
        if val.isdigit():
            spawn(TERMINAL + ["--class", "rolight-ai", "--title", f"pid {val}",
                              "sh", "-c",
                              f"ps -p {int(val)} -o pid,ppid,user,%cpu,%mem,etime,cmd; "
                              "printf '\\nEnter para cerrar…'; read"])
    elif kind == "sys-psutil":
        spawn(TERMINAL + ["--class", "rolight-ai", "--title", "psutil",
                          "sh", "-c", "pip install --user psutil; exec bash"])
    elif kind == "sys-ncdu":
        spawn(TERMINAL + ["--class", "rolight-ai", "--title", "ncdu", "ncdu", "/"])
    elif kind == "sys-btop":
        if shutil.which("btop"):
            spawn(TERMINAL + ["--class", "rolight-ai", "--title", "btop", "btop"])
        else:
            notify("Falta btop", "sudo pacman -S btop")
    elif kind == "clip-home":
        show_clipboard("")
    elif kind == "bt-home":
        show_bluetooth("")
    elif kind == "wifi-home":
        show_wifi("")
    elif kind == "vpn-home":
        show_vpn("")
    elif kind == "bw-home":
        show_bw("")
    else:
        show_query(text)


def initial_view():
    """Vista de arranque, para abrir directo en un modo (`rolight-rofi b`).

    rofi no deja precargar la caja de texto en script mode, así que el modo se
    pasa por ROLIGHT_MODE y se aplica solo en el primer pintado (ROFI_RETV=0).
    A partir de ahí manda lo que el usuario escriba.
    """
    mode = os.environ.get("ROLIGHT_MODE", "").strip().lower()
    return {
        "c": lambda: show_clipboard(""), "clip": lambda: show_clipboard(""),
        "b": lambda: show_bluetooth(""), "bt": lambda: show_bluetooth(""),
        "w": lambda: show_wifi(""), "wifi": lambda: show_wifi(""),
        "v": lambda: show_vpn(""), "vpn": lambda: show_vpn(""),
        "k": lambda: show_bw(""), "bw": lambda: show_bw(""),
        "e": lambda: show_sessions(""), "ses": lambda: show_sessions(""),
        "i": lambda: show_system(""), "sys": lambda: show_system(""),
        "sistema": lambda: show_system(""),
        "clipboard": lambda: show_clipboard(""), "bluetooth": lambda: show_bluetooth(""),
        "sesiones": lambda: show_sessions(""), "bitwarden": lambda: show_bw(""),
    }.get(mode, show_home)


def main():
    # `--theme DEST`: generar el tema con el placeholder del modo. Lo usa el
    # launcher, no rofi.
    if len(sys.argv) > 2 and sys.argv[1] == "--theme":
        return 0 if write_theme(sys.argv[2]) else 1

    retv = int(os.environ.get("ROFI_RETV", "0"))
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    info = os.environ.get("ROFI_INFO", "")
    if retv == 0:
        # rofi re-llama al script en cada tecla y pasa el texto en argv[1]. Con
        # una palabra de sistema mostramos los datos ya, sin lista ni Enter; con
        # cualquier otra cosa sigue el filtrado normal de la lista.
        if arg.strip().lower() in STAT_WORDS:
            return show_system()
        if arg.strip():
            return show_query(arg.strip())
        initial_view()()
    elif retv == 1 and info:
        handle_info(info, arg)
    elif arg.strip():
        show_query(arg.strip())
    else:
        show_home()


if __name__ == "__main__":
    main()
