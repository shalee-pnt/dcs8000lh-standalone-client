#!/usr/bin/env python3
"""
Client de vue en direct 100% autonome pour la camera D-Link DCS-8000LH.
Aucun telephone, aucun Frida requis : login cloud + negociation WebSocket +
dechiffrement SRTP (cle derivee des tokens de connexion) + relais vers ffplay.
"""
import asyncio
import getpass
import hashlib
import json
import os
import secrets
import tempfile
import socket
import subprocess
import sys
import time
import urllib.parse

import pylibsrtp
import requests
import urllib3
import websockets

urllib3.disable_warnings()

API_URL = "https://api.auto.mydlink.com"
CLIENT_ID_OAUTH = "mydlinkuapandroid"
ANDROID_ID = "bd36a6c011f1287e"
OAUTH_SECRET = "5259311fa8cab90f09f2dc1e09d2d8ee"
HEADERS = {
    "User-Agent": "Mozilla/5.0", "connection": "Keep-Alive", "accept": "*/*",
    "x-md-lang": "en", "x-md-app-ver": "02.00.01.75",
    "x-md-os-type": "android", "x-md-os-version": "7.0",
}

LOCAL_ADDR = os.environ.get("LOCAL_ADDR", "192.168.1.34")  # ton IP locale sur le LAN (a adapter)
LOCAL_PORT = 47000
FFPLAY = os.environ.get("FFPLAY", "ffplay")  # ffplay doit etre dans le PATH, ou definir $FFPLAY
SDP_PATH = os.path.join(tempfile.gettempdir(), "dcs8000lh.sdp")

VIDEO_RELAY_PORT = 49100
AUDIO_RELAY_PORT = 49102

SDP_TEMPLATE = """v=0
o=- 0 0 IN IP4 127.0.0.1
s=Camera decrypted (standalone)
t=0 0
m=video {video_port} RTP/AVP 96
c=IN IP4 127.0.0.1
a=rtpmap:96 H264/90000
a=fmtp:96 packetization-mode=1;profile-level-id=640028;sprop-parameter-sets=J2QAKKwa0AoAt0IAAAMAAgAAAwB4HihFQA==,KO4PJIk=
m=audio {audio_port} RTP/AVP 97
c=IN IP4 127.0.0.1
a=rtpmap:97 opus/16000
"""


def md5(s):
    return hashlib.md5(s.encode()).digest()


def stun_get_mapped_address(stun_host, stun_port, local_addr, local_port, timeout=3):
    import struct

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((local_addr, local_port))
    s.settimeout(timeout)

    magic_cookie = 0x2112A442
    txn_id = secrets.token_bytes(12)
    header = struct.pack(">HHI", 0x0001, 0, magic_cookie) + txn_id

    try:
        s.sendto(header, (socket.gethostbyname(stun_host), stun_port))
        data, _ = s.recvfrom(2048)
    finally:
        s.close()

    if len(data) < 20:
        raise RuntimeError("Reponse STUN trop courte")
    msg_type, msg_len = struct.unpack(">HH", data[0:4])
    if msg_type != 0x0101:
        raise RuntimeError(f"Type de reponse STUN inattendu: {msg_type:#x}")

    pos = 20
    mapped_ip = None
    mapped_port = None
    while pos + 4 <= len(data):
        attr_type, attr_len = struct.unpack(">HH", data[pos:pos + 4])
        attr_val = data[pos + 4:pos + 4 + attr_len]
        if attr_type == 0x0020 and len(attr_val) >= 8:  # XOR-MAPPED-ADDRESS
            xport = struct.unpack(">H", attr_val[2:4])[0] ^ (magic_cookie >> 16)
            xip_bytes = bytes(b ^ m for b, m in zip(attr_val[4:8], struct.pack(">I", magic_cookie)))
            mapped_ip = ".".join(str(b) for b in xip_bytes)
            mapped_port = xport
            break
        if attr_type == 0x0001 and len(attr_val) >= 8:  # MAPPED-ADDRESS (fallback)
            port = struct.unpack(">H", attr_val[2:4])[0]
            ip_bytes = attr_val[4:8]
            mapped_ip = ".".join(str(b) for b in ip_bytes)
            mapped_port = port
            break
        pos += 4 + attr_len + (attr_len % 4 and (4 - attr_len % 4) or 0)

    if mapped_ip is None:
        raise RuntimeError("Pas d'adresse mappee dans la reponse STUN")
    return mapped_ip, mapped_port


def cloud_login():
    print("Email du compte mydlink : ", end="", file=sys.stderr, flush=True)
    email = input().strip()
    password = getpass.getpass("Mot de passe (saisie masquee) : ", stream=sys.stderr)
    pw_hash = hashlib.md5(password.encode()).hexdigest()

    qs = (
        "/oauth/authorize2?client_id={cid}&redirect_uri={redir}&user_name={email}"
        "&password={pw}&response_type=token&timestamp={ts}&uc_id={aid}&uc_name=PythonCheck"
    ).format(
        cid=CLIENT_ID_OAUTH,
        redir=urllib.parse.quote("https://mydlink.com"),
        email=urllib.parse.quote(email),
        pw=pw_hash,
        ts=str(time.time())[:10],
        aid=ANDROID_ID,
    )
    sig = hashlib.md5((qs + OAUTH_SECRET).encode()).hexdigest()
    login_url = API_URL + qs + "&sig=" + sig

    r = requests.get(login_url, headers=HEADERS, verify=False, allow_redirects=True)
    params = {}
    for h in r.history:
        if h.status_code == 302:
            loc = h.headers.get("Location", "")
            params = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)

    if not params or "access_token" not in params:
        print("ECHEC CONNEXION:", r.text[:500], file=sys.stderr)
        sys.exit(1)

    access_token = params["access_token"][0]
    api_site = params["api_site"][0]
    print("Connexion cloud OK. api_site =", api_site, file=sys.stderr)

    device_list_url = f"https://{api_site}/me/device/list?access_token={access_token}"
    r2 = requests.get(device_list_url, headers=HEADERS, verify=False)
    devices = r2.json().get("data", [])
    if not devices:
        print("Aucun appareil trouve", file=sys.stderr)
        sys.exit(1)
    dev = devices[0]
    mac = dev["mac"]
    mydlink_id = dev["mydlink_id"]

    detail_url = f"https://{api_site}/me/device/info?access_token={access_token}"
    body = {"data": [{"mac": mac, "mydlink_id": mydlink_id}]}
    r3 = requests.post(detail_url, headers={**HEADERS, "Content-Type": "application/json"},
                        json=body, verify=False)
    detail = r3.json()["data"][0]

    return {
        "email": email,
        "access_token": access_token,
        "mac": mac,
        "device_token": detail["device_token"],
        "dcd_url": detail["DCD"],
    }


async def live_view(info, udp_video, udp_audio):
    """Une session de visionnage. Retourne quand le flux se coupe (stall) ou
    que la connexion tombe, pour permettre une renegociation par l'appelant."""
    import ssl
    seq = [1]

    def next_seq():
        seq[0] += 1
        return seq[0]

    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    print("Connexion au relais cloud:", info["dcd_url"], file=sys.stderr)
    async with websockets.connect(info["dcd_url"], ssl=ssl_ctx, open_timeout=15, ping_interval=None) as ws:
        sign_in = {
            "role": "client_agent",
            "owner_id": info["email"],
            "scope": ["user", "device:status", "device:control", "viewing", "photo", "policy", "client", "event"],
            "owner_token": info["access_token"],
            "client_name": "PythonStandalone",
            "command": "sign_in",
            "timestamp": int(time.time()),
            "sequence_id": next_seq(),
        }
        await ws.send(json.dumps(sign_in))
        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if resp.get("code") != 0:
            print("ECHEC sign_in cloud:", resp, file=sys.stderr)
            return
        client_id = resp["client_id"]
        print("Cloud sign_in OK, client_id =", client_id, file=sys.stderr)

        conn_init = {
            "device_id": info["mac"],
            "device_token": info["device_token"],
            "client_id": client_id,
            "command": "conn_init",
            "client_info": {"local_addr": LOCAL_ADDR, "local_port": LOCAL_PORT},
            "need_stream_info": 1,
            "sa_cmd": 1,
            "sequence_id": next_seq(),
            "timestamp": int(time.time()),
        }
        await ws.send(json.dumps(conn_init))
        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if resp.get("code") != 0:
            print("ECHEC conn_init:", resp, file=sys.stderr)
            return

        conn_id = resp["conn_id"]
        device_info = resp["device_info"]
        stream_info = resp["stream_info"][0]
        device_token_conn = device_info["device_token"]
        client_token_conn = device_info["client_token"]
        print(f"Camera locale: {device_info['local_addr']}:{device_info['local_port']}", file=sys.stderr)
        print(f"device_token (conn) = {device_token_conn}", file=sys.stderr)
        print(f"client_token (conn) = {client_token_conn}", file=sys.stderr)

        master_key = md5(device_token_conn + client_token_conn)
        master_salt = md5(client_token_conn + device_token_conn)[:14]
        print("master_key  =", master_key.hex(), file=sys.stderr)
        print("master_salt =", master_salt.hex(), file=sys.stderr)

        policy = pylibsrtp.Policy(
            key=master_key + master_salt,
            ssrc_type=pylibsrtp.Policy.SSRC_ANY_INBOUND,
            srtp_profile=pylibsrtp.Policy.SRTP_PROFILE_AES128_CM_SHA1_80,
        )
        srtp_session = pylibsrtp.Session(policy)

        try:
            pub_ip, pub_port = stun_get_mapped_address(
                "mp-eu-sas.auto.mydlink.com", 3478, LOCAL_ADDR, LOCAL_PORT
            )
            print(f"STUN: adresse publique = {pub_ip}:{pub_port}", file=sys.stderr)
        except Exception as e:
            print("STUN echec, fallback sur adresse locale:", e, file=sys.stderr)
            pub_ip, pub_port = LOCAL_ADDR, LOCAL_PORT

        conn_add_candidate = {
            "device_id": info["mac"],
            "device_token": info["device_token"],
            "client_id": client_id,
            "conn_id": conn_id,
            "command": "conn_add_candidate",
            "client_info": {"remote_addr": pub_ip, "remote_port": pub_port},
            "stun_info": {"host_addr": "mp-eu-sas.auto.mydlink.com", "host_port": 3478},
            "sa_ver": 1,
            "sequence_id": next_seq(),
            "timestamp": int(time.time()),
        }
        await ws.send(json.dumps(conn_add_candidate))
        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        print("conn_add_candidate ->", resp.get("code"), file=sys.stderr)

        loop = asyncio.get_event_loop()
        counts = {"video": 0, "audio": 0, "fail": 0, "heartbeat": 0, "raw": 0}
        last_video = {"t": loop.time()}

        camera_media_addr = {"addr": None}
        out_type = {"t": 0}          # type de nos heartbeats (0 puis 1 une fois la camera en type 2)
        _ct = client_token_conn.encode()
        _dt = device_token_conn.encode()

        def build_heartbeat(ptype):
            # 24 FF 00 15 | type | counter_BE(4) | MD5(counter_LE || client_token || device_token)[:16]
            r = secrets.token_bytes(4)          # counter_LE (ce qui est haché)
            digest = hashlib.md5(r + _ct + _dt).digest()[:16]
            return bytes([0x24, 0xFF, 0x00, 0x15, ptype]) + r[::-1] + digest

        def handle_packet(data, addr, transport_ref):
            counts["raw"] += 1
            if counts["raw"] <= 8:
                print(f"RAW #{counts['raw']} from {addr} {len(data)}o hex[:9]={data[:9].hex()}", file=sys.stderr)
            camera_media_addr["addr"] = addr
            if len(data) < 4 or data[0] != 0x24:
                counts["fail"] += 1
                return
            channel = data[1]
            frame_len = (data[2] << 8) | data[3]
            payload = data[4:4 + frame_len]
            if channel == 0xFF:
                counts["heartbeat"] += 1
                # Le client escalade proactivement vers type 1 apres les 2 premiers
                # heartbeats (c'est notre type 1 qui declenche le type 2 de la camera).
                if counts["heartbeat"] >= 2 and out_type["t"] == 0:
                    out_type["t"] = 1
                    print(">>> escalade proactive vers type 1", file=sys.stderr)
                transport_ref.sendto(build_heartbeat(out_type["t"]), addr)
                return
            try:
                plain = srtp_session.unprotect(payload)
            except Exception:
                counts["fail"] += 1
                return
            if len(plain) < 2:
                return
            pt = plain[1] & 0x7F
            if pt == 96:
                counts["video"] += 1
                last_video["t"] = loop.time()
                udp_video.sendto(plain, ("127.0.0.1", VIDEO_RELAY_PORT))
            elif pt == 97:
                counts["audio"] += 1
                udp_audio.sendto(plain, ("127.0.0.1", AUDIO_RELAY_PORT))
            total = counts["video"] + counts["audio"]
            grand_total = total + counts["fail"] + counts["heartbeat"]
            if grand_total % 20 == 0:
                print(f"  video={counts['video']} audio={counts['audio']} fail={counts['fail']} heartbeat={counts['heartbeat']}", file=sys.stderr)

        class MediaProtocol(asyncio.DatagramProtocol):
            def connection_made(self, transport):
                self.transport = transport

            def datagram_received(self, data, addr):
                handle_packet(data, addr, self.transport)

        media_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        media_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        media_sock.setblocking(False)
        media_sock.bind((LOCAL_ADDR, LOCAL_PORT))
        transport, protocol = await loop.create_datagram_endpoint(
            MediaProtocol, sock=media_sock
        )
        print(f"Ecoute UDP (asyncio) sur {LOCAL_ADDR}:{LOCAL_PORT}", file=sys.stderr)

        # Heartbeats proactifs vers l'endpoint media de la camera, pour amorcer
        # et maintenir le canal (la camera ne streame pas tant qu'elle ne recoit
        # pas de heartbeats valides de notre part).
        camera_media_addr["addr"] = (device_info["local_addr"], device_info["local_port"])
        for _ in range(3):
            transport.sendto(build_heartbeat(out_type["t"]), camera_media_addr["addr"])

        async def ping_sender():
            while True:
                await asyncio.sleep(1)
                if camera_media_addr["addr"]:
                    transport.sendto(build_heartbeat(out_type["t"]), camera_media_addr["addr"])

        ping_task = asyncio.create_task(ping_sender())

        session_id = secrets.token_hex(4).upper()
        rtsp_play = {
            "device_id": info["mac"],
            "device_token": info["device_token"],
            "client_id": client_id,
            "conn_id": conn_id,
            "command": "rtsp_command",
            "rtsp_id": 4,
            "fast_play": 1,
            "fast_play_media": [1, 1, 0],
            "rtsp_msg": (
                "PLAY rtsp://127.0.0.1/live/profile.0 RTSP/1.0\r\n"
                "CSeq: 5\r\n"
                "User-Agent: PythonStandalone\r\n"
                f"Session: {session_id}\r\n"
                "Range: npt=0.000-\r\n\r\n"
            ),
            "sequence_id": next_seq(),
            "timestamp": int(time.time()),
        }
        await ws.send(json.dumps(rtsp_play))

        rtsp_ok = False
        for _ in range(5):
            try:
                resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
            except asyncio.TimeoutError:
                break
            if resp.get("command") == "rtsp_command":
                print("PLAY response code:", resp.get("code"), file=sys.stderr)
                rtsp_ok = resp.get("code") == 0
                break

        if not rtsp_ok:
            print("PLAY non confirme, on tente d'ecouter quand meme...", file=sys.stderr)

        rtsp_cseq = [5]

        async def send_rtsp_options():
            rtsp_cseq[0] += 1
            msg = {
                "device_id": info["mac"],
                "device_token": info["device_token"],
                "client_id": client_id,
                "conn_id": conn_id,
                "command": "rtsp_command",
                "rtsp_id": 4,
                "rtsp_msg": (
                    "OPTIONS rtsp://127.0.0.1/live/profile.0 RTSP/1.0\r\n"
                    f"CSeq: {rtsp_cseq[0]}\r\n"
                    "User-Agent: PythonStandalone\r\n"
                    f"Session: {session_id}\r\n\r\n"
                ),
                "sequence_id": next_seq(),
                "timestamp": int(time.time()),
            }
            await ws.send(json.dumps(msg))

        async def keepalive_sender():
            while True:
                await asyncio.sleep(5)
                try:
                    await ws.send(json.dumps({
                        "command": "keep_alive",
                        "client_id": client_id,
                        "sequence_id": next_seq(),
                        "timestamp": int(time.time()),
                    }))
                    await send_rtsp_options()
                except Exception as e:
                    print("keepalive send failed:", e, file=sys.stderr)
                    return

        async def ws_drainer():
            while True:
                try:
                    msg = await ws.recv()
                except Exception:
                    return
                try:
                    d = json.loads(msg)
                    if d.get("command") not in ("keep_alive",):
                        print("WS<<", str(d)[:150], file=sys.stderr)
                except Exception:
                    pass

        keepalive_task = asyncio.create_task(keepalive_sender())
        drainer_task = asyncio.create_task(ws_drainer())
        try:
            # Surveille le flux : si pas de video pendant 4s (apres un demarrage),
            # on considere la session coupee et on retourne pour renegocier.
            started = False
            while True:
                await asyncio.sleep(0.5)
                idle = loop.time() - last_video["t"]
                if counts["video"] > 0:
                    started = True
                if started and idle > 4.0:
                    print(">>> flux coupe (stall), renegociation...", file=sys.stderr)
                    return
                if not started and idle > 20.0:
                    print(">>> aucun flux apres 20s, renegociation...", file=sys.stderr)
                    return
        finally:
            keepalive_task.cancel()
            drainer_task.cancel()
            ping_task.cancel()
            transport.close()


def main():
    info = cloud_login()

    # SDP + relais + ffplay lances UNE fois ; persistent a travers les renegociations.
    with open(SDP_PATH, "w", newline="\r\n") as f:
        f.write(SDP_TEMPLATE.format(video_port=VIDEO_RELAY_PORT, audio_port=AUDIO_RELAY_PORT))
    udp_video = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_audio = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    print("Lancement de ffplay (fenetre persistante)...", file=sys.stderr)
    ffplay = subprocess.Popen([
        FFPLAY,
        "-protocol_whitelist", "file,udp,rtp",
        "-probesize", "32768",
        "-analyzeduration", "0",
        "-max_delay", "5000000",   # 5s de tolerance de reordonnancement RTP (absorbe les rafales)
        "-sync", "ext",            # lecture sur horloge externe = fluide en direct, sans derive
        "-i", SDP_PATH,
    ])

    try:
        while ffplay.poll() is None:   # tant que la fenetre ffplay est ouverte
            try:
                asyncio.run(live_view(info, udp_video, udp_audio))
            except KeyboardInterrupt:
                break
            except Exception as e:
                print("session interrompue:", e, "-> reconnexion dans 1s", file=sys.stderr)
            time.sleep(1)
    finally:
        if ffplay.poll() is None:
            ffplay.terminate()
        print("Fin.", file=sys.stderr)


if __name__ == "__main__":
    main()
