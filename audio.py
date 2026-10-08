#!/usr/bin/env python3
"""Sonido para rolight: qué se está usando y cambiarlo fácil (PipeWire/PulseAudio).

- Salidas y micrófonos con nombres claros; al elegir uno pasa a ser el de por defecto
  y se mueven ahí las apps que están sonando/grabando. Si hace falta (parlantes vs
  auriculares de la notebook), cambia el perfil de la placa solo.
- Bluetooth: elegir el codec (AAC, SBC-XQ, SBC…) o el modo llamada con micrófono.
- Volumen y silencio de salida y micrófono.

También desde la terminal:  audio.py status | vol+ | vol- | mute | mic-mute
"""
import json
import os
import re
import subprocess
import sys
import time
from shutil import which

ENV = dict(os.environ, LC_ALL="C")  # nombres de perfil y JSON estables (sin traducir)
IGNORAR_APPS = {"speech-dispatcher-dummy", "speech-dispatcher", "PulseAudio Volume Control",
                "pavucontrol", "Pavucontrol", "wireplumber"}
NOMBRES_PUERTO = [  # (regex sobre el nombre del puerto ALSA, nombre claro)
    (r"speaker", "Parlantes de la notebook"),
    (r"headphone", "Auriculares con cable"),
    (r"hdmi\s*(\d*)|displayport", "Monitor por HDMI/DisplayPort"),
    (r"mic\s*1|digital mic|internal mic|dmic", "Micrófono de la notebook"),
    (r"mic\s*2|headset mic|external mic", "Micrófono con cable"),
    (r"line", "Entrada de línea"),
]


def available():
    return None if which("pactl") else "falta pactl (pipewire-pulse o pulseaudio-utils)"


def pactl(*args, timeout=4):
    return subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=timeout, env=ENV).stdout


def pjson(what):
    try:
        return json.loads(pactl("-f", "json", "list", what) or "[]")
    except ValueError:
        return []


def _vol(obj):
    try:
        return int(list(obj["volume"].values())[0]["value_percent"].rstrip("%"))
    except (KeyError, IndexError, ValueError):
        return 0


def _bt_label(desc):
    """'High Fidelity Playback (A2DP Sink, codec AAC)' → ('Música', 'AAC')."""
    codec = (re.search(r"codec ([^)]+)\)", desc) or [None, ""])[1]
    kind = "Llamada" if re.search(r"HSP|HFP|Head Unit", desc) else "Música"
    return kind, codec


def _port_name(port, desc):
    for rx, name in NOMBRES_PUERTO:
        m = re.search(rx, port, re.I)
        if m:
            if name.startswith("Monitor") and m.groups() and m.group(1) and m.group(1) != "1":
                return f"{name} {m.group(1)}"
            return name
    return desc


def _is_out(port):
    return "[Out]" in port or "output" in port.lower()


def state():
    cards, sinks, sources = pjson("cards"), pjson("sinks"), pjson("sources")
    inputs, outputs = pjson("sink-inputs"), pjson("source-outputs")
    dsink, dsource = pactl("get-default-sink").strip(), pactl("get-default-source").strip()
    by_index = {s["index"]: s for s in sinks}
    src_index = {s["index"]: s for s in sources}

    outs, mics, bts = [], [], []
    for c in cards:
        bt = c["name"].startswith("bluez_card")
        cname = c["properties"].get("device.description") or c["name"]
        if bt:
            profiles = []
            for pname, p in c["profiles"].items():
                if pname == "off" or not p.get("available", True):
                    continue
                kind, codec = _bt_label(p["description"])
                profiles.append({"profile": pname, "kind": kind, "codec": codec or pname,
                                 "active": pname == c["active_profile"], "mic": p.get("sources", 0) > 0})
            prof = c["profiles"].get(c["active_profile"], {})
            kind, codec = _bt_label(prof.get("description", ""))
            bts.append({"card": c["name"], "name": cname, "kind": kind, "codec": codec,
                        "profiles": sorted(profiles, key=lambda x: (x["kind"] != "Música", x["codec"]))})
            continue
        for port, p in c["ports"].items():
            if p.get("availability") == "not available":
                continue
            item = {"card": c["name"], "port": port, "name": _port_name(port, p["description"]),
                    "profiles": p.get("profiles", []), "in_profile": c["active_profile"] in p.get("profiles", [])}
            (outs if _is_out(port) else mics).append(item)

    def node_for(item, nodes):
        for n in nodes:
            if n.get("properties", {}).get("device.name") == item["card"].replace("alsa_card.", "alsa_card.") \
                    and any(pt["name"] == item["port"] for pt in n.get("ports", [])):
                return n
        for n in nodes:  # algunas versiones no publican device.name: por puerto y tarjeta
            if any(pt["name"] == item["port"] for pt in n.get("ports", [])) and \
                    item["card"].split(".", 1)[-1] in n["name"]:
                return n
        return None

    for o in outs:
        n = node_for(o, sinks)
        o.update(node=n and n["name"], volume=n and _vol(n), muted=n and n["mute"],
                 default=bool(n) and n["name"] == dsink, running=bool(n) and n["state"] == "RUNNING")
    for m in mics:
        n = node_for(m, sources)
        m.update(node=n and n["name"], volume=n and _vol(n), muted=n and n["mute"],
                 default=bool(n) and n["name"] == dsource)
    for b in bts:  # el Bluetooth entra como una salida (y un micrófono si hay)
        key = b["card"].replace("bluez_card.", "")
        sk = next((s for s in sinks if key.replace("_", ":") in s["name"] or key in s["name"]), None)
        sr = next((s for s in sources if (key.replace("_", ":") in s["name"] or key in s["name"])
                   and s.get("monitor_of_sink") in (None, "n/a")), None)
        outs.append({"card": b["card"], "port": None, "name": b["name"], "bt": b, "node": sk and sk["name"],
                     "volume": sk and _vol(sk), "muted": sk and sk["mute"], "in_profile": bool(sk),
                     "default": bool(sk) and sk["name"] == dsink, "running": bool(sk) and sk["state"] == "RUNNING"})
        if sr:
            mics.append({"card": b["card"], "port": None, "name": f"Micrófono de {b['name']}", "bt": b,
                         "node": sr["name"], "volume": _vol(sr), "muted": sr["mute"], "in_profile": True,
                         "default": sr["name"] == dsource})

    def apps(streams, key, nodes):
        out = {}
        for s in streams:
            name = s["properties"].get("application.name") or s["properties"].get("media.name") or "?"
            if name in IGNORAR_APPS or s.get("corked"):
                continue
            node = nodes.get(s[key])
            if node and not node["name"].endswith(".monitor"):
                out.setdefault(node["name"], []).append(name)
        return {k: sorted(set(v)) for k, v in out.items()}

    playing = apps(inputs, "sink", by_index)
    recording = apps(outputs, "source", src_index)
    for o in outs:
        o["apps"] = playing.get(o["node"], [])
    for m in mics:
        m["apps"] = recording.get(m["node"], [])
    return {"outs": outs, "mics": mics, "bt": bts}


# ── acciones ─────────────────────────────────────────────────────────
def _wait_node(kind, item, tries=20):
    for _ in range(tries):
        s = state()
        for x in s["outs" if kind == "sink" else "mics"]:
            if x["card"] == item["card"] and x.get("port") == item.get("port") and x["node"]:
                return x["node"]
        time.sleep(0.1)
    return None


def use(kind, item):
    """Hace de `item` la salida/entrada por defecto y mueve ahí lo que esté sonando/grabando."""
    node = item.get("node")
    if not node and item.get("port"):  # el puerto es de otro perfil de la placa: cambiarlo
        pactl("set-card-profile", item["card"], item["profiles"][0])
        node = _wait_node(kind, item)
    if not node:
        raise RuntimeError(f"No encontré {item['name']}")
    if item.get("port"):
        pactl(f"set-{kind}-port", node, item["port"])
    pactl(f"set-default-{kind}", node)
    streams = "sink-inputs" if kind == "sink" else "source-outputs"
    for s in pjson(streams):
        pactl(f"move-{'sink-input' if kind == 'sink' else 'source-output'}", str(s["index"]), node)
    return node


def bt_profile(card, profile):
    pactl("set-card-profile", card, profile)


def volume(delta, kind="sink"):
    target = "@DEFAULT_AUDIO_SINK@" if kind == "sink" else "@DEFAULT_AUDIO_SOURCE@"
    if which("wpctl"):
        subprocess.run(["wpctl", "set-volume", "-l", "1.0", target, f"{abs(delta)}%{'+' if delta > 0 else '-'}"],
                       capture_output=True, timeout=3)
    else:
        pactl(f"set-{kind}-volume", f"@DEFAULT_{kind.upper()}@", f"{'+' if delta > 0 else '-'}{abs(delta)}%")


def mute(kind="sink"):
    pactl(f"set-{kind}-mute", f"@DEFAULT_{kind.upper()}@", "toggle")


def resumen(st):
    o = next((x for x in st["outs"] if x["default"]), None)
    m = next((x for x in st["mics"] if x["default"]), None)
    out = f"🔊 {o['name']} {o['volume']}%" + (" (mute)" if o["muted"] else "") if o else "🔊 —"
    if o and o.get("bt"):
        out += f" · {o['bt']['codec']}"
    mic = f"🎙 {m['name']}" + (" (mute)" if m["muted"] else "") if m else "🎙 —"
    return f"{out}   {mic}"


def main(argv):
    cmd = argv[0] if argv else "status"
    if (msg := available()):
        sys.exit(msg)
    if cmd == "status":
        print(resumen(state()))
    elif cmd in ("vol+", "vol-"):
        volume(5 if cmd == "vol+" else -5)
    elif cmd == "mute":
        mute("sink")
    elif cmd == "mic-mute":
        mute("source")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
