#!/usr/bin/env python3
"""
Se connecte au Gadget Frida (deja attache dans l'app patchee sur le telephone),
charge le hook srtp_unprotect, et relaie chaque paquet RTP dechiffre vers un
socket UDP local, en separant video (payload type 96) et audio (97), pour que
ffplay puisse lire le flux via un fichier SDP local.
"""
import os
import socket
import tempfile
import subprocess
import sys
import time

import frida

VIDEO_PORT = 46000
AUDIO_PORT = 46002
FFPLAY = os.environ.get("FFPLAY", "ffplay")
SDP_PATH = os.path.join(tempfile.gettempdir(), "dcs8000lh_decrypted.sdp")
SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "frida_srtp_hook.js")

SDP_TEMPLATE = """v=0
o=- 0 0 IN IP4 127.0.0.1
s=Camera decrypted
t=0 0
m=video {video_port} RTP/AVP 96
c=IN IP4 127.0.0.1
a=rtpmap:96 H264/90000
a=fmtp:96 packetization-mode=1;profile-level-id=640028;sprop-parameter-sets=J2QAKKwa0AoAt0IAAAMAAgAAAwB4HihFQA==,KO4PJIk=
m=audio {audio_port} RTP/AVP 97
c=IN IP4 127.0.0.1
a=rtpmap:97 opus/16000
"""

udp_video = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
udp_audio = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

counts = {"video": 0, "audio": 0, "other": 0, "fail": 0}


def on_message(message, data):
    if message["type"] != "send":
        print("non-send message:", message)
        return
    payload = message["payload"]
    if payload.get("kind") == "unprotect_fail":
        counts["fail"] += 1
        return
    if payload.get("kind") != "rtp" or not data:
        return
    if len(data) < 2:
        return
    pt = data[1] & 0x7F
    if pt == 96:
        counts["video"] += 1
        udp_video.sendto(data, ("127.0.0.1", VIDEO_PORT))
    elif pt == 97:
        counts["audio"] += 1
        udp_audio.sendto(data, ("127.0.0.1", AUDIO_PORT))
    else:
        counts["other"] += 1
    total = counts["video"] + counts["audio"]
    if total % 20 == 0 and total > 0:
        print(f"  ... video={counts['video']} audio={counts['audio']} other={counts['other']} fail={counts['fail']}", file=sys.stderr)


def main():
    with open(SDP_PATH, "w", newline="\r\n") as f:
        f.write(SDP_TEMPLATE.format(video_port=VIDEO_PORT, audio_port=AUDIO_PORT))
    print("SDP ecrit:", SDP_PATH, file=sys.stderr)

    device = frida.get_device_manager().add_remote_device("127.0.0.1:27042")
    print("Peripherique Frida:", device, file=sys.stderr)
    session = device.attach("Gadget")
    print("Attache au process Gadget.", file=sys.stderr)

    with open(SCRIPT_PATH, "r", encoding="utf-8") as f:
        src = f.read()
    script = session.create_script(src)
    script.on("message", on_message)
    script.load()
    print("Script charge. Lance le direct maintenant sur le telephone.", file=sys.stderr)

    print("Lancement de ffplay en ecoute...", file=sys.stderr)
    ffplay = subprocess.Popen([FFPLAY, "-protocol_whitelist", "file,udp,rtp", "-i", SDP_PATH])

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        ffplay.terminate()


if __name__ == "__main__":
    main()
