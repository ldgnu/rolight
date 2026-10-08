#!/usr/bin/env python3
"""Música para rolight con minitone como motor.

minitone es la dependencia: rolight busca en las mismas fuentes (Radio Browser y
YouTube vía yt-dlp), lee y escribe sus favoritos/historial, y si minitone está
abierto controla su reproductor (mpv) en lugar de levantar otro.
Cuando minitone no está abierto, rolight usa un mpv propio en segundo plano
con el mismo formato de canciones que minitone.

También se usa desde la terminal o atajos de sway:
    music.py play jazz      busca radios y reproduce la primera
    music.py toggle | next | prev | stop | vol+ | vol-
    music.py status [--json]
"""
import datetime
import glob
import http.client
import json
import os
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from shutil import which

CONF = os.path.expanduser("~/.config/minitone")
CACHE = os.path.expanduser("~/.cache/rolight")
STATE = os.path.join(CACHE, "music.json")
SOCK = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or CACHE, "rolight-mpv.sock")
# espejos de Radio Browser; nl1 y at1 (los de minitone) dejaron de responder en 2026-10.
# "all" es el round-robin DNS oficial: si un espejo muere, sigue andando.
RB_HOSTS = ("de1", "de2", "all")
UA = "rolight/1.0 (minitone)"


def available():
    """None si está todo; si no, el texto de lo que falta."""
    if not which("minitone"):
        return "minitone no está instalado"
    if not which("mpv"):
        return "falta mpv (lo necesita minitone)"
    return None


def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def config():
    return _load(os.path.join(CONF, "config.json"), {})


# ── mpv IPC ──────────────────────────────────────────────────────────
def ipc(sock, *cmd, timeout=0.5):
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(timeout)
    try:
        s.connect(sock)
        s.sendall(json.dumps({"command": list(cmd)}).encode() + b"\n")
        buf = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                raise ConnectionError("mpv cerró el socket")
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                msg = json.loads(line)
                if "event" in msg:
                    continue
                if msg.get("error") != "success":
                    raise RuntimeError(msg.get("error"))
                return msg.get("data")
    finally:
        s.close()


def prop(sock, name, default=None):
    try:
        return ipc(sock, "get_property", name)
    except Exception:  # noqa: BLE001
        return default


def alive(sock):
    try:
        ipc(sock, "get_property", "pid")
        return True
    except Exception:  # noqa: BLE001
        return False


def minitone_socks():
    return [s for s in glob.glob("/tmp/minitone-mpv-*/ipc.sock") if alive(s)]


def minitone_running():
    return subprocess.run(["pgrep", "-x", "minitone"], capture_output=True).returncode == 0


def active():
    """(socket, dueño) del reproductor a mostrar: el que esté sonando, rolight primero."""
    own = SOCK if alive(SOCK) else None
    mt = minitone_socks()
    for sock, owner in [(own, "rolight")] + [(s, "minitone") for s in mt]:
        if sock and prop(sock, "idle-active") is False:
            return sock, owner
    return None, None


def ensure_player():
    if alive(SOCK):
        return SOCK
    try:
        os.unlink(SOCK)
    except OSError:
        pass
    vol = str(_load(STATE, {}).get("volume") or config().get("volume", 70))
    args = ["mpv", "--no-video", "--no-terminal", "--idle=yes", "--keep-open=no",
            f"--input-ipc-server={SOCK}", f"--volume={vol}", "--title=rolight-music"]
    if ipv4_ok():
        args.append("--ytdl-raw-options-append=force-ipv4=")  # el yt-dlp de mpv, ver «red: IPv4 primero»
    subprocess.Popen(args,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    for _ in range(40):
        if alive(SOCK):
            return SOCK
        time.sleep(0.05)
    raise RuntimeError("mpv no arrancó")


# ── red: IPv4 primero ────────────────────────────────────────────────
# Con un IPv6 roto (router que anuncia ruta pero no llega a internet) urllib y yt-dlp
# prueban primero la dirección IPv6 y se quedan colgados hasta el timeout: las búsquedas
# tardaban 15 s (radios) o no volvían nunca (YouTube). Los navegadores no lo notan porque
# prueban IPv4 e IPv6 en paralelo. Si hay ruta IPv4, se usa primero; si no, IPv6 como siempre.
_V4 = None


def ipv4_ok():
    """¿Hay ruta IPv4 a internet? (connect de UDP no manda paquetes)."""
    global _V4
    if _V4 is None:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("1.1.1.1", 53))
            _V4 = True
        except OSError:
            _V4 = False
    return _V4


def _connect_v4_first(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, **_kw):
    host, port = address
    infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    if ipv4_ok():
        infos.sort(key=lambda i: i[0] != socket.AF_INET)
    err = None
    for family, kind, proto, _canon, addr in infos:
        sock = socket.socket(family, kind, proto)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(addr)
            return sock
        except OSError as e:
            err = e
            sock.close()
    raise err or OSError(f"no se pudo conectar a {host}")


class _HTTPSv4(http.client.HTTPSConnection):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._create_connection = _connect_v4_first


class _HandlerV4(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_HTTPSv4, req, context=self._context)


_opener = urllib.request.build_opener(_HandlerV4())


# ── fuentes (mismo formato de canción que minitone) ──────────────────
def _get(url, timeout=6):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with _opener.open(req, timeout=timeout) as r:
        return json.load(r)


def _radio_song(st, q=""):
    tags = [t.strip() for t in (st.get("tags") or "").split(",") if t.strip()]
    tags.sort(key=lambda t: q.lower() not in t.lower())  # el género buscado primero
    song = {"id": f"radio:{st['stationuuid']}", "source": "radio", "source_id": st["stationuuid"],
            "title": st.get("name", "").strip(), "artist": st.get("country") or st.get("countrycode", ""),
            "url": st.get("url_resolved") or st.get("url")}
    if st.get("bitrate"):
        song["bitrate"] = st["bitrate"]
    if st.get("codec"):
        song["format"] = st["codec"]
    if tags:
        song["genre"] = tags[0]
    return song


def _relevance(st, q):
    """Popularidad, premiando el nombre que coincide y las radios dedicadas al género."""
    ntags = len([t for t in (st.get("tags") or "").split(",") if t.strip()]) or 1
    named = q.lower() in st.get("name", "").lower()
    return (st.get("clickcount", 0) + 1) * (4 if named else 1) / ntags ** 0.5


def search_radio(q, limit=30):
    base = "&hidebroken=true&order=clickcount&reverse=true&limit=" + str(limit)
    err = None
    for host in RB_HOSTS:
        api = f"https://{host}.api.radio-browser.info/json/stations/search?"
        try:
            by_tag = _get(api + "tag=" + urllib.parse.quote(q) + base)
            by_name = _get(api + "name=" + urllib.parse.quote(q) + base)
        except Exception as e:  # noqa: BLE001
            err = e
            continue
        seen, out = set(), []
        for st in sorted(by_tag + by_name, key=lambda s: -_relevance(s, q)):
            if st["stationuuid"] not in seen and (st.get("url_resolved") or st.get("url")):
                seen.add(st["stationuuid"])
                out.append(_radio_song(st, q))
        return out[:limit]
    raise err or RuntimeError("Radio Browser no responde")


def search_youtube(q, limit=12):
    if not which("yt-dlp"):
        raise RuntimeError("yt-dlp no está instalado")
    cmd = ["yt-dlp", "--flat-playlist", "-j", "--no-warnings", "--no-update"]
    if ipv4_ok():
        cmd.append("--force-ipv4")  # ver «red: IPv4 primero»
    out = subprocess.run(cmd + [f"ytsearch{limit}:{q}"],
                         capture_output=True, text=True, timeout=25).stdout
    songs = []
    for line in out.splitlines():
        v = json.loads(line)
        song = {"id": f"yt:{v['id']}", "source": "youtube", "source_id": v["id"],
                "title": v.get("title", ""), "artist": v.get("channel") or v.get("uploader") or "",
                "url": f"https://www.youtube.com/watch?v={v['id']}"}
        if v.get("duration"):
            song["duration"] = int(v["duration"])
        songs.append(song)
    return songs


def _songs(entries):
    out, seen = [], set()
    for e in entries if isinstance(entries, list) else []:
        s = e.get("song", e) if isinstance(e, dict) else None
        if s and s.get("url") and s.get("id") not in seen:
            seen.add(s.get("id"))
            out.append(s)
    return out


def favorites():
    return _songs(_load(os.path.join(CONF, "favorites.json"), []))


def history():
    return _songs(_load(os.path.join(CONF, "history.json"), []))


def _add_history(song):
    # si minitone está abierto, él es dueño del archivo y lo pisaría: no tocamos
    if minitone_running():
        return
    path = os.path.join(CONF, "history.json")
    hist = [e for e in _load(path, []) if isinstance(e, dict)]
    hist.insert(0, {"song": song, "played_at": datetime.datetime.now().astimezone().isoformat()})
    _save(path, hist[:200])


def _click(song):
    """Avisa a Radio Browser que se escuchó la radio (como hacen los clientes)."""
    if song.get("source") == "radio":
        try:
            _get(f"https://{RB_HOSTS[0]}.api.radio-browser.info/json/url/{song['source_id']}", 3)
        except Exception:  # noqa: BLE001
            pass


# ── control ──────────────────────────────────────────────────────────
def play(queue, idx=0):
    song = queue[idx]
    sock = ensure_player()
    for s in minitone_socks():  # que no suenen dos cosas a la vez
        try:
            ipc(s, "set_property", "pause", True)
        except Exception:  # noqa: BLE001
            pass
    # toda la lista va a mpv: así anterior/siguiente andan también desde playerctl
    # (Alt+J/K vía mpv-mpris) aunque rolight esté cerrado
    ipc(sock, "loadfile", queue[0]["url"], "replace", timeout=2)
    for sg in queue[1:]:
        ipc(sock, "loadfile", sg["url"], "append")
    if idx:
        ipc(sock, "set_property", "playlist-pos", idx)
    ipc(sock, "set_property", "pause", False)
    state = _load(STATE, {})
    state.update(queue=queue, idx=idx)
    _save(STATE, state)
    _add_history(song)
    _click(song)
    return song


def step(delta):
    """Siguiente/anterior en la lista de rolight. False si no hay a dónde ir."""
    sock, owner = active()
    if owner == "minitone":
        return False
    st = _load(STATE, {})
    queue = st.get("queue") or []
    idx = _pos(sock, st) + delta
    if not sock or not 0 <= idx < len(queue):
        return False
    ipc(sock, "set_property", "playlist-pos", idx)
    ipc(sock, "set_property", "pause", False)
    st["idx"] = idx
    _save(STATE, st)
    _add_history(queue[idx])
    _click(queue[idx])
    return True


def _pos(sock, st):
    """Posición real en la lista de mpv (pudo moverse con playerctl); si no, la guardada."""
    pos = prop(sock, "playlist-pos") if sock else None
    return pos if isinstance(pos, int) and pos >= 0 else st.get("idx", 0)


def toggle():
    sock, _ = active()
    if not sock and alive(SOCK) and _load(STATE, {}).get("queue"):
        st = _load(STATE, {})  # se detuvo: retomar lo último
        return play(st["queue"], st.get("idx", 0)) and True
    if sock:
        ipc(sock, "cycle", "pause")
        return True
    return False


def stop():
    sock, owner = active()
    if not sock:
        return
    if owner == "rolight":
        ipc(sock, "stop")
    else:
        ipc(sock, "set_property", "pause", True)


def volume(delta):
    sock, owner = active()
    if not sock:
        return None
    ipc(sock, "add", "volume", delta)
    vol = prop(sock, "volume")
    if owner == "rolight" and vol is not None:
        st = _load(STATE, {})
        st["volume"] = round(vol)
        _save(STATE, st)
    return vol


def status():
    """Lo que sabemos de lo que está sonando, o None."""
    sock, owner = active()
    if not sock:
        return None
    meta = prop(sock, "metadata") or {}
    title = meta.get("icy-title") or prop(sock, "media-title") or ""
    st = _load(STATE, {}) if owner == "rolight" else {}
    queue, idx = st.get("queue") or [], _pos(sock, st) if owner == "rolight" else 0
    song = queue[idx] if owner == "rolight" and 0 <= idx < len(queue) else {}
    station = song.get("title") or meta.get("icy-name") or ""
    if title == station or title == os.path.basename(prop(sock, "path") or ""):
        title = ""
    br = prop(sock, "audio-bitrate") or 0
    return {
        "owner": owner, "source": song.get("source") or ("radio" if meta.get("icy-name") else ""),
        "station": station or title, "title": title if station else "",
        "artist": song.get("artist", ""), "genre": song.get("genre") or meta.get("icy-genre", ""),
        "paused": bool(prop(sock, "pause")), "volume": round(prop(sock, "volume") or 0),
        "kbps": round(br / 1000) if br else song.get("bitrate") or int(meta.get("icy-br") or 0),
        "codec": (prop(sock, "audio-codec-name") or song.get("format") or "").upper(),
        "elapsed": prop(sock, "time-pos") or 0,
        "duration": prop(sock, "duration") if song.get("source") == "youtube" else None,
        "index": idx if song else None, "total": len(queue) if song else None,
    }


def clock(sec):
    sec = int(sec or 0)
    h, m, s = sec // 3600, sec // 60 % 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def line(s):
    if not s:
        return "⏹ nada sonando"
    now = f"{s['station']} — {s['title']}" if s["title"] else s["station"]
    return f"{'⏸' if s['paused'] else '▶'} {now}"


def main(argv):
    cmd, rest = (argv[0] if argv else "status"), " ".join(argv[1:]).strip()
    if (msg := available()):
        sys.exit(msg)
    if cmd == "play":
        songs = search_radio(rest or "jazz")
        if not songs:
            sys.exit(f"sin radios para «{rest}»")
        print(line({"station": play(songs)["title"], "title": "", "paused": False}))
    elif cmd == "toggle":
        toggle()
    elif cmd in ("next", "prev"):
        if not step(1 if cmd == "next" else -1):
            sys.exit("no hay más en la lista")
    elif cmd == "stop":
        stop()
    elif cmd in ("vol+", "vol-"):
        volume(5 if cmd == "vol+" else -5)
    elif cmd == "status":
        s = status()
        if "--json" in argv:  # formato waybar
            print(json.dumps({"text": line(s), "class": "paused" if s and s["paused"] else "playing" if s else "stopped",
                              "tooltip": json.dumps(s, ensure_ascii=False, indent=1) if s else ""}, ensure_ascii=False))
        else:
            print(line(s))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
