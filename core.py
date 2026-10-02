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
import configparser
import datetime as dt
import glob
import json
import math
import operator
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo, available_timezones

# ── Ajustes ──────────────────────────────────────────────────────────
WEB_SEARCH = "https://www.google.com/search?q={}"
TERMINAL = ["kitty"]
AI_BACKEND = "opencode"                  # "opencode" o "claude"
WEATHER_CITY = ""                        # vacío = detecta por IP
FILE_SEARCH_ROOT = os.path.expanduser("~")
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
        cmd = [fd, "--ignore-case", "--max-results", str(MAX_FILES), "--exclude", "node_modules",
               "--exclude", ".git", "--", q, FILE_SEARCH_ROOT]
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


# ── Pantallas ────────────────────────────────────────────────────────
POWER = [
    ("Bloquear pantalla", "lock", "system-lock-screen", "lock bloquear"),
    ("Suspender", "suspend", "system-suspend", "suspend sleep dormir"),
    ("Cerrar sesión", "logout", "system-log-out", "logoff logout salir exit"),
    ("Reiniciar", "reboot", "system-reboot", "reboot restart reinicio"),
    ("Apagar", "poweroff", "system-shutdown", "shutdown poweroff apagar"),
]


def show_home():
    header()
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
    header(f"¿Qué hago con «{esc(t)}»?")
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


def do_power(act):
    cmds = {
        "lock": ["swaylock", "-f", "-c", "000000"],
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
    else:
        show_query(text)


def main():
    retv = int(os.environ.get("ROFI_RETV", "0"))
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    info = os.environ.get("ROFI_INFO", "")
    if retv == 0:
        show_home()
    elif retv == 1 and info:
        handle_info(info, arg)
    elif arg.strip():
        show_query(arg.strip())
    else:
        show_home()


if __name__ == "__main__":
    main()
