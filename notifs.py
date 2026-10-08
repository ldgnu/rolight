#!/usr/bin/env python3
"""Notificaciones de dunst para rolight y waybar.

El modo «n» de rolight muestra el historial (las nuevas arriba), silencia, activa
«no molestar» por un rato y limpia. Abrirlo marca todo como visto.

También se usa desde la terminal o la barra:
    notifs.py waybar          JSON para un módulo custom de waybar (campanita)
    notifs.py mute | dnd [min] | seen | clear
"""
import json
import os
import re
import subprocess
import sys
import time
from shutil import which

CACHE = os.path.expanduser("~/.cache/rolight")
SEEN = os.path.join(CACHE, "notifs-visto")    # último id que viste
UNTIL = os.path.join(CACHE, "notifs-hasta")   # fin del «no molestar» (epoch)
SIGNAL = 9                                    # pkill -RTMIN+9 waybar refresca la campanita


def available():
    return None if which("dunstctl") else "dunst no está instalado"


def _ctl(*args, timeout=3):
    return subprocess.run(["dunstctl", *args], capture_output=True, text=True, timeout=timeout).stdout


def _read(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def _write(path, text):
    os.makedirs(CACHE, exist_ok=True)
    with open(path, "w") as f:
        f.write(str(text))


def refresh_bar():
    subprocess.run(["pkill", f"-RTMIN+{SIGNAL}", "-x", "waybar"], capture_output=True)


def plain(text):
    """dunst guarda el markup: lo sacamos para mostrar y copiar."""
    text = re.sub(r"<[^>]+>", "", text or "")
    for a, b in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'"), ("&amp;", "&")):
        text = text.replace(a, b)
    return text.strip()


def history():
    """Lo más nuevo primero: dicts con id, app, summary, body, urgency, icon, age (segundos)."""
    try:
        data = json.loads(_ctl("history") or "{}").get("data", [[]])[0]
    except ValueError:
        return []
    now = time.monotonic()  # dunst guarda el timestamp en µs de CLOCK_MONOTONIC
    out = []
    for e in data:
        v = {k: (x.get("data") if isinstance(x, dict) else x) for k, x in e.items()}
        out.append({"id": v.get("id", 0), "app": v.get("appname", ""), "summary": plain(v.get("summary")),
                    "body": plain(v.get("body")), "urgency": (v.get("urgency") or "").lower(),
                    "icon": v.get("icon_path") or "", "age": max(0, now - v.get("timestamp", 0) / 1e6)})
    return sorted(out, key=lambda n: -n["id"])


def seen_id():
    try:
        return int(_read(SEEN, "0"))
    except ValueError:
        return 0


def mark_seen(items=None):
    items = history() if items is None else items
    if items:
        _write(SEEN, max(n["id"] for n in items))
    refresh_bar()


def paused():
    return _ctl("is-paused").strip() == "true"


def waiting():
    m = re.search(r"Waiting:\s*(\d+)", _ctl("count"))
    return int(m.group(1)) if m else 0


def dnd_until():
    try:
        until = float(_read(UNTIL, "0"))
    except ValueError:
        return None
    return until if until > time.time() else None


def set_paused(on):
    _ctl("set-paused", "true" if on else "false")
    if not on:
        try:
            os.unlink(UNTIL)
        except OSError:
            pass
    refresh_bar()


def toggle_mute():
    set_paused(not paused())


def dnd(minutes=60):
    """Silencia por un rato; si después lo tocás a mano, el temporizador no te pisa."""
    until = time.time() + minutes * 60
    _write(UNTIL, until)
    _ctl("set-paused", "true")
    script = (f'sleep {minutes * 60}; [ "$(cat {UNTIL} 2>/dev/null)" = "{until}" ] && '
              f'dunstctl set-paused false && rm -f {UNTIL} && pkill -RTMIN+{SIGNAL} -x waybar')
    subprocess.Popen(["sh", "-c", script], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    refresh_bar()


def clear():
    _ctl("history-clear")
    mark_seen([])
    try:
        os.unlink(SEEN)
    except OSError:
        pass


def remove(nid):
    _ctl("history-rm", str(nid))
    refresh_bar()


def ago(sec):
    sec = int(sec)
    if sec < 60:
        return "recién"
    if sec < 3600:
        return f"hace {sec // 60} min"
    if sec < 86400:
        return f"hace {sec // 3600} h"
    return f"hace {sec // 86400} d"


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def waybar():
    hist = history()
    seen = seen_id()
    new = [n for n in hist if n["id"] > seen]
    if paused():
        until = dnd_until()
        w = waiting()
        tip = "No molestar" + (f" hasta las {time.strftime('%H:%M', time.localtime(until))}" if until else "")
        tip += f"\n{w} esperando" if w else ""
        return {"text": "<span alpha='70%'>󰂛</span>", "tooltip": tip + "\n\nclick derecho: activar",
                "class": "silenciadas"}
    if not new:
        return {"text": "<span alpha='55%'>󰂜</span>", "tooltip": "Sin notificaciones nuevas\n\nclick: historial · "
                "derecho: silenciar", "class": "vacias"}
    crit = any(n["urgency"] == "critical" for n in new)
    lines = [f"{'⚠ ' if n['urgency'] == 'critical' else '· '}{esc(n['summary'] or n['app'])}"
             f"  <span alpha='55%'>{ago(n['age'])}</span>" for n in new[:6]]
    more = f"\n<span alpha='55%'>y {len(new) - 6} más</span>" if len(new) > 6 else ""
    return {"text": f"󰂚 <small>{len(new)}</small>", "class": "criticas" if crit else "nuevas",
            "tooltip": "\n".join(lines) + more + "\n\nclick: historial · derecho: silenciar · medio: marcar vistas"}


def main(argv):
    cmd = argv[0] if argv else "waybar"
    if (msg := available()):
        sys.exit(msg)
    if cmd == "waybar":
        print(json.dumps(waybar(), ensure_ascii=False))
    elif cmd == "mute":
        toggle_mute()
    elif cmd == "dnd":
        dnd(int(argv[1]) if len(argv) > 1 else 60)
    elif cmd == "seen":
        mark_seen()
    elif cmd == "clear":
        clear()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
