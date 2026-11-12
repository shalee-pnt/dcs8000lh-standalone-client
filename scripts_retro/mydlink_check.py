#!/usr/bin/env python3
"""
Script minimal et transparent : se connecte a l'API cloud mydlink (celle
utilisee par l'appli officielle Android) avec TON compte, puis liste les
appareils que D-Link voit cote serveur. N'envoie rien nulle part ailleurs
qu'aux serveurs mydlink.com (les memes que l'appli officielle contacte).
Rien n'est enregistre, le mot de passe n'est jamais affiche ni ecrit.
"""
import getpass
import hashlib
import sys
import time
import json
import urllib.parse
import requests
import urllib3
urllib3.disable_warnings()

API_URL = "https://api.auto.mydlink.com"
CLIENT_ID = "mydlinkuapandroid"
ANDROID_ID = "bd36a6c011f1287e"
OAUTH_SECRET = "5259311fa8cab90f09f2dc1e09d2d8ee"
HEADERS = {
    "User-Agent": "Mozilla/5.0", "connection": "Keep-Alive", "accept": "*/*",
    "x-md-lang": "en", "x-md-app-ver": "02.00.01.75",
    "x-md-os-type": "android", "x-md-os-version": "7.0",
}

def md5(v):
    return hashlib.md5(v.encode("utf-8")).hexdigest()

def main():
    print("Email du compte mydlink : ", end="", file=sys.stderr, flush=True)
    email = input().strip()
    password = getpass.getpass("Mot de passe (saisie masquee) : ", stream=sys.stderr)
    pw_hash = md5(password)

    qs = (
        "/oauth/authorize2?client_id={cid}&redirect_uri={redir}&user_name={email}"
        "&password={pw}&response_type=token&timestamp={ts}&uc_id={aid}&uc_name=PythonCheck"
    ).format(
        cid=CLIENT_ID,
        redir=urllib.parse.quote("https://mydlink.com"),
        email=urllib.parse.quote(email),
        pw=pw_hash,
        ts=str(time.time())[:10],
        aid=ANDROID_ID,
    )
    sig = md5(qs + OAUTH_SECRET)
    login_url = API_URL + qs + "&sig=" + sig

    print("\n--- Connexion a", API_URL, "---")
    r = requests.get(login_url, headers=HEADERS, verify=False, allow_redirects=True)
    print("Statut final:", r.status_code)
    print("Historique de redirections:", [h.status_code for h in r.history])

    params = {}
    for h in r.history:
        if h.status_code == 302:
            loc = h.headers.get("Location", "")
            params = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)

    if not params or "access_token" not in params:
        print("\nECHEC DE CONNEXION. Reponse brute du serveur (peut aider a diagnostiquer) :")
        print(r.text[:1000])
        return

    access_token = params["access_token"][0]
    api_site = params["api_site"][0]
    print("Connexion reussie. Site API attribue:", api_site)

    device_list_url = f"https://{api_site}/me/device/list?access_token={access_token}"
    r2 = requests.get(device_list_url, headers=HEADERS, verify=False)
    print("\n--- Liste des appareils cote serveur D-Link ---")
    print("Statut:", r2.status_code)
    devices = []
    try:
        data = r2.json()
        print(json.dumps(data, indent=2, ensure_ascii=False))
        devices = data.get("data", [])
    except Exception:
        print(r2.text[:2000])
        return

    if not devices:
        print("\nAucun appareil trouve.")
        return

    dev = devices[0]
    mac = dev.get("mac")
    mydlink_id = dev.get("mydlink_id")
    print(f"\n--- Details du premier appareil (mac={mac}) ---")
    detail_url = f"https://{api_site}/me/device/info?access_token={access_token}"
    body = {"data": [{"mac": mac, "mydlink_id": mydlink_id}]}
    r3 = requests.post(detail_url, headers={**HEADERS, "Content-Type": "application/json"},
                        json=body, verify=False)
    print("Statut:", r3.status_code)
    try:
        detail_data = r3.json()
        print(json.dumps(detail_data, indent=2, ensure_ascii=False))
    except Exception:
        print(r3.text[:2000])

if __name__ == "__main__":
    main()
