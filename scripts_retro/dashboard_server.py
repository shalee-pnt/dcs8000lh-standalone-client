#!/usr/bin/env python3
"""
Dashboard local pour la DCS-8000LH, 100% independant du cloud payant D-Link.
Reutilise live_view_standalone (login cloud gratuit -> SRTP -> flux dechiffre),
mais au lieu d'ffplay branche ffmpeg -> MJPEG, fait sa PROPRE detection de
mouvement (difference d'images) + detection jour/nuit, et sert un tableau de
bord web sur http://127.0.0.1:8099 .

Lancement :
  venv\\Scripts\\python.exe dashboard_server.py
Puis ouvrir http://127.0.0.1:8099 dans un navigateur.
"""
import asyncio
import io
import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from collections import deque

import numpy as np
from PIL import Image
from aiohttp import web

import live_view_standalone as lv

# ---- Reglages ----
HTTP_HOST = "127.0.0.1"
HTTP_PORT = 8099
MJPEG_W, MJPEG_H = 960, 540      # resolution servie au navigateur
MJPEG_FPS = 20
DETECT_W, DETECT_H = 160, 90     # resolution d'analyse mouvement (petit = rapide)
PIXEL_DELTA = 22                 # seuil de variation par pixel (0-255)
MOTION_ON_DEFAULT = 0.018        # fraction de pixels qui bougent -> presence (reglable via UI)
MOTION_OFF_RATIO = 0.45          # seuil de sortie = MOTION_ON * ce ratio (hysteresis)
MOTION_END_HOLD = 4.0            # secondes de calme avant de clore un evenement
NIGHT_SAT = 12                   # saturation moyenne en dessous de laquelle = nuit
MAX_CAPTURES = 600               # nettoyage : nombre max de snapshots conserves sur disque

HERE = os.path.dirname(os.path.abspath(__file__))
VIDEO_SDP_PATH = os.path.join(HERE, "cam_video.sdp")
AUDIO_SDP_PATH = os.path.join(HERE, "cam_audio.sdp")
CONFIG_PATH = os.path.join(HERE, "dashboard_config.json")
CAPTURES_DIR = os.path.join(HERE, "captures")
DB_PATH = os.path.join(HERE, "dashboard_events.db")
if os.environ.get("FFMPEG"):
    FFMPEG = os.environ["FFMPEG"]
else:
    _base = os.path.basename(lv.FFPLAY).lower()
    if "ffplay" in _base:
        FFMPEG = os.path.join(os.path.dirname(lv.FFPLAY), _base.replace("ffplay", "ffmpeg"))
    else:
        FFMPEG = "ffmpeg"


# ---------------- Reglages persistants (sensibilite + zones de detection) ----------------

class Settings:
    """Sensibilite et zones de detection, persistees dans un JSON et appliquees
    en direct. Les zones sont des rectangles normalises (0..1) ; liste vide =
    toute l'image est surveillee."""

    def __init__(self):
        self.motion_on = MOTION_ON_DEFAULT
        self.motion_off = MOTION_ON_DEFAULT * MOTION_OFF_RATIO
        self.zones = []
        self.mask = None  # numpy bool (DETECT_H, DETECT_W) ou None = image entiere
        self.load()

    def load(self):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            self.motion_on = float(d.get("motion_on", MOTION_ON_DEFAULT))
            self.zones = d.get("zones", []) or []
        except Exception:
            pass
        self.motion_off = self.motion_on * MOTION_OFF_RATIO
        self._rebuild_mask()

    def save(self):
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump({"motion_on": self.motion_on, "zones": self.zones}, f, indent=2)
        except Exception as e:
            print("impossible d'enregistrer la config:", e, file=sys.stderr)

    def _rebuild_mask(self):
        if not self.zones:
            self.mask = None
            return
        m = np.zeros((DETECT_H, DETECT_W), dtype=bool)
        for z in self.zones:
            try:
                x0, y0, x1, y1 = z
                xa, xb = sorted((int(x0 * DETECT_W), int(x1 * DETECT_W)))
                ya, yb = sorted((int(y0 * DETECT_H), int(y1 * DETECT_H)))
                m[max(0, ya):min(DETECT_H, yb + 1), max(0, xa):min(DETECT_W, xb + 1)] = True
            except Exception:
                continue
        self.mask = m if m.any() else None

    def sensitivity(self):
        """Valeur 1..10 (10 = tres sensible) derivee de motion_on, pour l'UI."""
        lo, hi = 0.004, 0.05
        s = 1 + (hi - self.motion_on) / (hi - lo) * 9
        return round(max(1.0, min(10.0, s)), 1)

    def set_sensitivity(self, s):
        s = max(1.0, min(10.0, float(s)))
        lo, hi = 0.004, 0.05
        self.motion_on = hi - (s - 1) / 9.0 * (hi - lo)
        self.motion_off = self.motion_on * MOTION_OFF_RATIO

    def set_zones(self, zones):
        clean = []
        for z in (zones or [])[:8]:
            try:
                x0, y0, x1, y1 = [float(v) for v in z]
                x0, x1 = sorted((max(0.0, min(1.0, x0)), max(0.0, min(1.0, x1))))
                y0, y1 = sorted((max(0.0, min(1.0, y0)), max(0.0, min(1.0, y1))))
                if x1 - x0 > 0.01 and y1 - y0 > 0.01:
                    clean.append([x0, y0, x1, y1])
            except Exception:
                continue
        self.zones = clean
        self._rebuild_mask()


settings = Settings()


# ---------------- Historique persistant (SQLite) ----------------

db_lock = threading.Lock()


def db_init():
    os.makedirs(CAPTURES_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("""CREATE TABLE IF NOT EXISTS events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER NOT NULL,
        type TEXT NOT NULL,
        level REAL,
        duration REAL,
        snapshot TEXT
    )""")
    conn.commit()
    return conn


db_conn = db_init()


def db_insert_event(ev):
    with db_lock:
        cur = db_conn.execute(
            "INSERT INTO events(ts,type,level,duration,snapshot) VALUES (?,?,?,?,?)",
            (ev["ts"], ev["type"], ev.get("level"), ev.get("duration"), ev.get("snapshot")))
        db_conn.commit()
        return cur.lastrowid


def _row_to_event(row):
    id_, ts, type_, level, duration, snapshot = row
    d = {"id": id_, "kind": "event", "ts": ts, "type": type_}
    if level is not None:
        d["level"] = level
    if duration is not None:
        d["duration"] = duration
    if snapshot:
        d["snapshot"] = snapshot
    return d


def db_recent_events(limit=20, before_id=None):
    with db_lock:
        if before_id:
            cur = db_conn.execute(
                "SELECT id,ts,type,level,duration,snapshot FROM events WHERE id < ? ORDER BY id DESC LIMIT ?",
                (int(before_id), limit))
        else:
            cur = db_conn.execute(
                "SELECT id,ts,type,level,duration,snapshot FROM events ORDER BY id DESC LIMIT ?", (limit,))
        rows = cur.fetchall()
    return [_row_to_event(r) for r in rows]


def save_snapshot(jpg):
    """Sauvegarde l'image declenchant une presence. Nettoyage simple au-dela
    de MAX_CAPTURES fichiers pour ne pas grossir indefiniment."""
    try:
        os.makedirs(CAPTURES_DIR, exist_ok=True)
        fname = time.strftime("%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}.jpg"
        with open(os.path.join(CAPTURES_DIR, fname), "wb") as f:
            f.write(jpg)
        if save_snapshot.counter % 25 == 0:
            _cleanup_captures()
        save_snapshot.counter += 1
        return fname
    except Exception:
        return None


save_snapshot.counter = 0


def _cleanup_captures():
    try:
        files = sorted(
            (os.path.join(CAPTURES_DIR, f) for f in os.listdir(CAPTURES_DIR)),
            key=os.path.getmtime)
        excess = len(files) - MAX_CAPTURES
        for f in files[:max(0, excess)]:
            os.remove(f)
    except Exception:
        pass


class State:
    def __init__(self):
        self.latest_jpeg = None
        self.frame_seq = 0
        self.motion_level = 0.0
        self.motion_active = False
        self.is_night = False
        self.status = "connexion"       # connexion | direct | reconnexion
        self.events = deque(db_recent_events(20), maxlen=60)  # recents, plus recent en tete
        self.frames = 0
        self.last_frame_t = 0.0
        self.loop = None
        self.sse_queues = set()          # manipule uniquement dans le thread de la loop
        self.audio_queues = set()        # clients /audio.mp3 (thread loop)

state = State()


def now_ms():
    return int(time.time() * 1000)


def dispatch(ev):
    """Pousse un evenement/maj d'etat vers tous les clients SSE (thread loop).
    Les evenements sont persistes en base (survie aux redemarrages)."""
    if ev.get("kind") == "event":
        eid = db_insert_event(ev)
        ev = dict(ev)
        ev["id"] = eid
        state.events.appendleft(ev)
    for q in list(state.sse_queues):
        try:
            q.put_nowait(ev)
        except Exception:
            pass


def push_from_thread(ev):
    if state.loop is not None:
        state.loop.call_soon_threadsafe(dispatch, ev)


def set_status(s):
    if state.status != s:
        state.status = s
        push_from_thread({"kind": "status", "status": s, "ts": now_ms()})


# ---------------- Detection de mouvement (thread lecteur ffmpeg) ----------------

def frame_reader(ff):
    """Lit le MJPEG sur la sortie d'ffmpeg, met a jour l'image courante et
    calcule la detection de mouvement + jour/nuit. Tourne dans un thread."""
    prev_small = None
    buf = b""
    motion_started_at = None
    last_above = 0.0
    frame_i = 0
    stdout = ff.stdout
    while True:
        chunk = stdout.read(65536)
        if not chunk:
            break
        buf += chunk
        while True:
            soi = buf.find(b"\xff\xd8")
            if soi < 0:
                break
            eoi = buf.find(b"\xff\xd9", soi + 2)
            if eoi < 0:
                if soi > 0:
                    buf = buf[soi:]
                break
            jpg = buf[soi:eoi + 2]
            buf = buf[eoi + 2:]
            state.latest_jpeg = jpg
            state.frame_seq += 1
            state.frames += 1
            state.last_frame_t = time.time()
            frame_i += 1
            try:
                _analyze(jpg, frame_i)
            except Exception:
                pass
    # ffmpeg termine
    return


def _analyze(jpg, frame_i):
    img = Image.open(io.BytesIO(jpg))
    small = img.resize((DETECT_W, DETECT_H))
    gray = np.asarray(small.convert("L"), dtype=np.int16)

    prev = _analyze.prev
    level = 0.0
    if prev is not None:
        diff = np.abs(gray - prev)
        hit = diff > PIXEL_DELTA
        mask = settings.mask
        if mask is not None:
            hit = hit & mask
            denom = int(mask.sum()) or 1
        else:
            denom = DETECT_W * DETECT_H
        level = float(np.count_nonzero(hit)) / denom
    _analyze.prev = gray
    state.motion_level = level

    t = time.time()
    # Machine a etats avec hysteresis (seuils reglables depuis l'UI)
    if not state.motion_active:
        if level >= settings.motion_on:
            state.motion_active = True
            _analyze.started = t
            _analyze.last_above = t
            ev = {"kind": "event", "type": "presence_start", "ts": now_ms(), "level": round(level, 4)}
            snap = save_snapshot(jpg)
            if snap:
                ev["snapshot"] = snap
            push_from_thread(ev)
            push_from_thread({"kind": "motion", "active": True, "level": round(level, 4)})
    else:
        if level >= settings.motion_off:
            _analyze.last_above = t
        if t - _analyze.last_above >= MOTION_END_HOLD:
            dur = round(t - (_analyze.started or t), 1)
            state.motion_active = False
            push_from_thread({"kind": "event", "type": "presence_end",
                              "ts": now_ms(), "duration": dur})
            push_from_thread({"kind": "motion", "active": False, "level": round(level, 4)})

    # Jour/nuit : saturation moyenne (toutes les ~20 frames pour economiser)
    if frame_i % 20 == 0:
        hsv = np.asarray(small.convert("HSV"))
        sat = float(hsv[:, :, 1].mean())
        night = sat < NIGHT_SAT
        if night != state.is_night:
            state.is_night = night
            push_from_thread({"kind": "event",
                              "type": "night_on" if night else "night_off",
                              "ts": now_ms()})
            push_from_thread({"kind": "daynight", "night": night})
    # throttle des maj de niveau (SSE) a ~3/s
    if frame_i % max(1, MJPEG_FPS // 3) == 0:
        push_from_thread({"kind": "level", "level": round(level, 4)})


_analyze.prev = None
_analyze.started = None
_analyze.last_above = 0.0


def launch_ffmpeg_video():
    return subprocess.Popen(
        [FFMPEG,
         "-hide_banner", "-loglevel", "error",
         # --- options d'entree : latence mini ---
         "-probesize", "32768", "-analyzeduration", "0",
         "-fflags", "nobuffer+discardcorrupt", "-flags", "low_delay",
         "-avioflags", "direct",
         "-protocol_whitelist", "file,udp,rtp",
         "-max_delay", "200000",            # 0.2s de reordonnancement (au lieu de 1s)
         "-i", VIDEO_SDP_PATH,
         # --- sortie MJPEG poussee image par image ---
         "-an",
         "-vf", f"scale={MJPEG_W}:{MJPEG_H},fps={MJPEG_FPS}",
         "-c:v", "mjpeg", "-q:v", "7",
         "-flush_packets", "1",             # ecrit chaque image aussitot (pas de buffer pipe)
         "-f", "mjpeg", "pipe:1"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)


def launch_ffmpeg_audio():
    return subprocess.Popen(
        [FFMPEG,
         "-hide_banner", "-loglevel", "error",
         "-probesize", "32768", "-analyzeduration", "0",
         "-fflags", "nobuffer", "-flags", "low_delay",
         "-protocol_whitelist", "file,udp,rtp",
         "-max_delay", "200000",
         "-i", AUDIO_SDP_PATH,
         "-vn",
         "-ar", "48000", "-ac", "1",
         "-flush_packets", "1",
         "-f", "s16le", "pipe:1"],   # PCM brut : latence maitrisee cote navigateur
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)


def audio_reader(ff):
    """Lit le PCM brut (s16le 48k mono) d'ffmpeg et le diffuse aux clients /audio.pcm."""
    stdout = ff.stdout
    while True:
        chunk = stdout.read(2048)
        if not chunk:
            break
        if state.audio_queues and state.loop is not None:
            state.loop.call_soon_threadsafe(dispatch_audio, chunk)


def dispatch_audio(chunk):
    for q in list(state.audio_queues):
        try:
            q.put_nowait(chunk)
        except asyncio.QueueFull:
            try:            # client en retard : on jette le plus ancien pour garder le direct
                q.get_nowait()
                q.put_nowait(chunk)
            except Exception:
                pass
        except Exception:
            pass


# ---------------- Boucle camera (asyncio, reutilise live_view) ----------------

async def camera_loop(info, udp_video, udp_audio):
    while True:
        set_status("connexion")
        ff = launch_ffmpeg_video()
        ffa = launch_ffmpeg_audio()
        threading.Thread(target=frame_reader, args=(ff,), daemon=True).start()
        threading.Thread(target=audio_reader, args=(ffa,), daemon=True).start()
        set_status("direct")
        try:
            await lv.live_view(info, udp_video, udp_audio, ff)
        except Exception as e:
            print("session interrompue:", e, "-> reconnexion", file=sys.stderr)
        finally:
            _analyze.prev = None
            for p in (ff, ffa):
                if p.poll() is None:
                    p.terminate()
                    try:
                        p.wait(timeout=3)
                    except Exception:
                        p.kill()
        set_status("reconnexion")
        await asyncio.sleep(2)   # laisse la camera liberer l'ancienne session


# ---------------- Serveur web ----------------

async def h_index(request):
    with open(os.path.join(HERE, "dashboard.html"), "r", encoding="utf-8") as f:
        return web.Response(text=f.read(), content_type="text/html")


async def h_state(request):
    return web.json_response({
        "status": state.status,
        "motion_active": state.motion_active,
        "motion_level": round(state.motion_level, 4),
        "is_night": state.is_night,
        "frames": state.frames,
        "events": list(state.events)[:40],
    })


async def h_mjpeg(request):
    boundary = "frameboundary"
    resp = web.StreamResponse(status=200, headers={
        "Content-Type": f"multipart/x-mixed-replace; boundary={boundary}",
        "Cache-Control": "no-cache, no-store, must-revalidate",
    })
    await resp.prepare(request)
    try:
        last_seq = -1
        while True:
            seq = state.frame_seq
            if seq != last_seq and state.latest_jpeg:
                last_seq = seq
                jpg = state.latest_jpeg
                await resp.write(
                    (f"--{boundary}\r\nContent-Type: image/jpeg\r\n"
                     f"Content-Length: {len(jpg)}\r\n\r\n").encode() + jpg + b"\r\n")
            else:
                await asyncio.sleep(0.006)   # attente courte, pousse des qu'une image arrive
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    except Exception:
        pass
    return resp


async def h_audio(request):
    resp = web.StreamResponse(status=200, headers={
        "Content-Type": "application/octet-stream",
        "Cache-Control": "no-cache, no-store",
        "Connection": "keep-alive",
    })
    await resp.prepare(request)
    q = asyncio.Queue(maxsize=64)
    state.audio_queues.add(q)
    try:
        while True:
            chunk = await q.get()
            await resp.write(chunk)
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        state.audio_queues.discard(q)
    return resp


async def h_get_settings(request):
    return web.json_response({
        "sensitivity": settings.sensitivity(),
        "zones": settings.zones,
    })


async def h_post_settings(request):
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "json invalide"}, status=400)
    if "sensitivity" in body:
        try:
            settings.set_sensitivity(body["sensitivity"])
        except Exception:
            pass
    if "zones" in body and isinstance(body["zones"], list):
        settings.set_zones(body["zones"])
    settings.save()
    return web.json_response({
        "sensitivity": settings.sensitivity(),
        "zones": settings.zones,
    })


async def h_events_history(request):
    """Historique paginé (bouton "charger plus" du journal)."""
    try:
        limit = max(1, min(100, int(request.query.get("limit", 30))))
    except ValueError:
        limit = 30
    before_id = request.query.get("before_id")
    evs = db_recent_events(limit, before_id)
    return web.json_response({"events": evs})


async def h_events(request):
    resp = web.StreamResponse(status=200, headers={
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
    })
    await resp.prepare(request)
    q = asyncio.Queue()
    state.sse_queues.add(q)
    try:
        # etat initial
        await resp.write(_sse({"kind": "status", "status": state.status}))
        await resp.write(_sse({"kind": "daynight", "night": state.is_night}))
        await resp.write(_sse({"kind": "motion", "active": state.motion_active,
                               "level": round(state.motion_level, 4)}))
        for ev in list(state.events)[:20][::-1]:
            await resp.write(_sse(ev))
        while True:
            ev = await q.get()
            await resp.write(_sse(ev))
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        state.sse_queues.discard(q)
    return resp


def _sse(obj):
    import json
    return ("data: " + json.dumps(obj) + "\n\n").encode()


async def start_background(app):
    state.loop = asyncio.get_event_loop()
    info = app["info"]
    # SDP complet puis scinde en video-seul / audio-seul (deux ffmpeg -> deux ports).
    full = lv.SDP_TEMPLATE.format(video_port=lv.VIDEO_RELAY_PORT,
                                  audio_port=lv.AUDIO_RELAY_PORT)
    head = full[:full.index("m=video")]
    vid = full[full.index("m=video"):full.index("m=audio")]
    aud = full[full.index("m=audio"):]
    with open(VIDEO_SDP_PATH, "w", newline="\r\n") as f:
        f.write(head + vid)
    with open(AUDIO_SDP_PATH, "w", newline="\r\n") as f:
        f.write(head + aud)
    udp_video = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_audio = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    app["cam_task"] = asyncio.create_task(camera_loop(info, udp_video, udp_audio))


async def cleanup_background(app):
    t = app.get("cam_task")
    if t:
        t.cancel()


def main():
    info = lv.cloud_login()
    print(f"\nTableau de bord : http://{HTTP_HOST}:{HTTP_PORT}\n", file=sys.stderr)
    app = web.Application()
    app["info"] = info
    app.router.add_get("/", h_index)
    app.router.add_get("/api/state", h_state)
    app.router.add_get("/stream.mjpeg", h_mjpeg)
    app.router.add_get("/audio.pcm", h_audio)
    app.router.add_get("/events", h_events)
    app.router.add_get("/api/events", h_events_history)
    app.router.add_get("/api/settings", h_get_settings)
    app.router.add_post("/api/settings", h_post_settings)
    os.makedirs(CAPTURES_DIR, exist_ok=True)
    app.router.add_static("/captures/", CAPTURES_DIR, show_index=False)
    app.on_startup.append(start_background)
    app.on_cleanup.append(cleanup_background)
    web.run_app(app, host=HTTP_HOST, port=HTTP_PORT, print=None)


if __name__ == "__main__":
    main()
