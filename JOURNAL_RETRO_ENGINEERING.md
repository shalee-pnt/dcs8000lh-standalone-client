# Journal de rétro-ingénierie — Caméra D-Link DCS-8000LH

Caméra : DCS-8000LH, mac `AABBCCDDEEFF`, mydlink_id `<mydlink_id>`, fw `2.07.01`,
hw_ver `A3` (label physique lu comme A1, mais l'API cloud dit A3).
Compte mydlink : `<ton-email@exemple.com>`.
Objectif : construire une "couche par-dessus" (dashboard custom) incluant le
direct vidéo, sans dépendre de l'appli officielle.

Ce fichier résume tout ce qui a été découvert et fait. **Les scripts les plus
importants ont été copiés dans `scripts_retro/` à côté de ce fichier** (ils
restent fonctionnels, sauf `live_view_standalone.py` qui pointe vers des
chemins `ffplay.exe`/SDP dans le scratchpad de session — à adapter si on
relance depuis une nouvelle session). Les autres scripts (Frida, exploration
APK, etc. non copiés ici) restent dans le scratchpad de session ci-dessous et
seront perdus si cette session se termine.

Chemin du scratchpad de la session où tout a été fait :
`<dossier de travail>/`

## 1. État des lieux — ce qui marche à 100%

### 1.1 API cloud mydlink (REST)
Entièrement fonctionnelle, utilisée pour login + infos appareil.

- Login OAuth : `GET https://api.auto.mydlink.com/oauth/authorize2?...`
  - `client_id=mydlinkuapandroid`, `android_id (uc_id)=bd36a6c011f1287e`
  - `sig = md5(querystring + OAUTH_SECRET)`, `OAUTH_SECRET=5259311fa8cab90f09f2dc1e09d2d8ee`
  - mot de passe envoyé en `md5(password)`
  - redirection 302 donne `access_token` + `api_site` (ex: `mp-eu-openapi.auto.mydlink.com`)
- Liste appareils : `GET https://{api_site}/me/device/list?access_token=...`
- Détails appareil : `POST https://{api_site}/me/device/info?access_token=...`
  avec body `{"data":[{"mac":..., "mydlink_id":...}]}`
  → retourne entre autres : `device_token` (cloud, format `MAC-uuid`, ex.
  `AABBCCDDEEFF-398f5c34-0972-07ed-7a0a-f6e671e96b1b`), `DCD` (URL du relais
  cloud websocket, ex `wss://mp-eu-dcdda.auto.mydlink.com:443/SwitchCamera`),
  `da_url` (websocket local direct, ex `wss://192.168.1.28:8080/SwitchCamera`),
  `local_liveview` (URL HLS locale `http://192.168.1.28:8088/live.m3u8` —
  protégée par Digest Auth, identifiants admin inconnus, **ne pas essayer de
  deviner les identifiants, un filtre de sécurité bloque le brute force**),
  `pin_code` (ex `<pin_code>`, code imprimé sur l'appareil).
- Script existant : `mydlink_check.py` (login interactif + dump JSON).
  Important : `input()`/`getpass` doivent écrire leur prompt sur `stderr`,
  sinon le prompt se perd si stdout est redirigé vers un fichier.

### 1.2 Protocole JSON de signalisation cloud (WebSocket DCD)
Entièrement reverse-engineeré et répliqué avec succès dans
`live_view_standalone.py` (voir §3). Séquence :

1. Connexion TLS WebSocket à l'URL `DCD` (ex.
   `wss://mp-eu-dcdda.auto.mydlink.com:443/SwitchCamera`), cert à ignorer
   (`ssl.CERT_NONE`) — **IMPORTANT : passer `ping_interval=None` à
   `websockets.connect()`, le serveur ne répond pas aux ping protocole
   standard, seulement aux messages applicatifs — sinon la connexion meurt
   après ~20-40s avec `keepalive ping timeout`.**
2. `sign_in` cloud :
   ```json
   {"role":"client_agent","owner_id":"<email>","scope":["user","device:status","device:control","viewing","photo","policy","client","event"],
    "owner_token":"<access_token OAuth>","client_name":"...","command":"sign_in","timestamp":...,"sequence_id":...}
   ```
   réponse : `{"client_id": "<uuid propre a cette connexion cloud>", "code":0,...}`
3. `conn_init` :
   ```json
   {"device_id":"<MAC>","device_token":"<cloud device_token>","client_id":"<client_id>",
    "command":"conn_init","client_info":{"local_addr":"<notre IP LAN>","local_port":<port choisi>},
    "need_stream_info":1,"sa_cmd":1,"sequence_id":...,"timestamp":...}
   ```
   réponse contient :
   - `conn_id` (entier, à réutiliser pour tous les messages suivants de cette session)
   - `device_info`: `{"local_addr":"<IP caméra, même LAN>","local_port":<port caméra>,
     "device_token":"<32 car hex>","client_token":"<32 car hex>"}` — **CES DEUX
     TOKENS SONT LA CLÉ DE TOUT, voir §2 chiffrement SRTP.**
   - `relay_info`: pareil mais pour un chemin relais cloud (non utilisé ici)
   - `stream_info`: tableau avec le SDP complet (codecs H264/opus, params SPS/PPS)
4. `conn_add_candidate` (échange ICE-like) :
   ```json
   {"device_id":...,"device_token":"<cloud token>","client_id":...,"conn_id":...,
    "command":"conn_add_candidate","client_info":{"remote_addr":"<IP publique>","remote_port":<port>},
    "stun_info":{"host_addr":"mp-eu-sas.auto.mydlink.com","host_port":3478},"sa_ver":1,...}
   ```
   On peut faire une vraie requête STUN (RFC5389 Binding Request) vers ce
   serveur pour obtenir une vraie IP:port publique (voir fonction
   `stun_get_mapped_address` dans `live_view_standalone.py`) — **mais ça n'a
   pas réglé le blocage final (§4), probablement pas la vraie cause.**
5. `rtsp_command` avec `fast_play:1` pour lancer le PLAY sans négociation
   RTSP complète :
   ```json
   {"device_id":...,"device_token":"<cloud token>","client_id":...,"conn_id":...,
    "command":"rtsp_command","rtsp_id":4,"fast_play":1,"fast_play_media":[1,1,0],
    "rtsp_msg":"PLAY rtsp://127.0.0.1/live/profile.0 RTSP/1.0\r\nCSeq: 5\r\nUser-Agent: ...\r\nSession: <8 hex aleatoires>\r\nRange: npt=0.000-\r\n\r\n",
    "sequence_id":...,"timestamp":...}
   ```
   réponse (ou poussée séparément) : `rtsp_msg` contenant
   `"RTSP/1.0 200 OK\r\n...Session: <vraie session serveur>\r\n..."` → PLAY accepté.
6. Keepalive nécessaires en continu (sinon le serveur de signalisation ferme,
   même si techniquement on répond aux ping — voir §4, pas suffisant à lui
   seul pour la vidéo) :
   - `{"command":"keep_alive","client_id":...,"sequence_id":...,"timestamp":...}`
     toutes les ~5-10s sur le websocket.
   - Egalement envoyer `OPTIONS` en `rtsp_command` périodiquement (comme le
     fait la vraie app) — implémenté, le serveur répond bien
     (`RTSP/1.0 200 OK ... Public: OPTIONS, DESCRIBE, SETUP, ...`), donc la
     connexion signalisation reste saine très longtemps avec ces deux
     mécanismes.

### 1.3 Script `live_view_standalone.py` — **FONCTIONNE INTÉGRALEMENT**
Fait TOUT en pur Python, **sans téléphone ni Frida** : login cloud, signalisation
WebSocket (sign_in/conn_init/conn_add_candidate/PLAY), heartbeats de contrôle
(§4), déchiffrement SRTP (§2), relais vers ffplay. Affiche la vidéo en direct
1280x720 30fps + audio. Lancer dans un terminal (mot de passe interactif), fermer
la fenêtre ffplay pour arrêter.

## 2. Chiffrement vidéo — SRTP — **CASSÉ ET VÉRIFIÉ**

La vidéo/audio réelle est transportée en **SRTP standard (RFC 3711)**, profil
`AES-128-CM + HMAC-SHA1-80` (= `srtp_crypto_policy_set_rtp_default` /
`_rtcp_default` de libsrtp, lesquelles correspondent à ce profil par défaut).

### Format des paquets sur le réseau local (port donné dans `client_info.local_port`)
Chaque datagramme UDP a un **préfixe de 4 octets avant les données SRTP** :
```
0x24 (caractère '$')  |  channel (1 octet)  |  longueur (2 octets big-endian)  |  payload (longueur octets)
```
- `channel = 0xFF` → paquet de "heartbeat"/contrôle, chiffré avec une AUTRE
  clé (liée à `sa_util_encrypt`/`sa_util_decrypt`, voir §4 — pas élucidé
  complètement, pas nécessaire pour la vidéo).
- `channel = 0x20` (et apparemment aussi 0x21/0x22/0x23 pendant une poignée de
  main initiale) → ces canaux portent les vrais paquets **SRTP** (RTP standard
  chiffré). `payload` est directement un paquet SRTP valide (passer tel quel
  à `srtp_unprotect`/`pylibsrtp`).

### Dérivation de la clé SRTP — FORMULE CONFIRMÉE EMPIRIQUEMENT
À partir de `device_token` et `client_token` reçus dans `device_info` de la
réponse `conn_init` (les tokens courts 32 caractères hex, PAS le
`device_token` cloud format `MAC-uuid`) :

```python
import hashlib
def md5(s): return hashlib.md5(s.encode()).digest()

master_key  = md5(device_token + client_token)          # 16 octets, utilisés tels quels
master_salt = md5(client_token + device_token)[:14]      # 14 premiers octets du second hash

# Avec pylibsrtp (pip install pylibsrtp) :
import pylibsrtp
policy = pylibsrtp.Policy(
    key=master_key + master_salt,   # 30 octets concatenes
    ssrc_type=pylibsrtp.Policy.SSRC_ANY_INBOUND,
    srtp_profile=pylibsrtp.Policy.SRTP_PROFILE_AES128_CM_SHA1_80,
)
session = pylibsrtp.Session(policy)
plain_rtp = session.unprotect(payload)  # payload = les octets APRES le prefixe $+channel+len
```

**Vérifié en comparant avec un vrai paquet capturé via Frida** (hook natif sur
`srtp_unprotect` dans `libsa.so`, capturant le ciphertext en entrée ET le
plaintext en sortie) — `pylibsrtp.unprotect()` avec cette formule redonne
EXACTEMENT le même plaintext. Voir script `test_key_derivation.py`.

Une fois décrypté, chaque paquet est un paquet RTP standard. Démultiplexage
simple par `payload[1] & 0x7F` : `96` = vidéo H264, `97` = audio opus.

### Comment on a trouvé cette formule
1. `libsa.so` (bibliothèque native ARM64 de l'appli, dans
   `lib/arm64-v8a/libsa.so` de l'APK — extraite depuis le split
   `split_config.arm64_v8a.apk` d'un APK Android App Bundle) embarque
   **OpenSSL ET libsrtp** statiquement (fonctions exportées
   `srtp_unprotect`, `srtp_create`, `srtp_add_stream`,
   `srtp_crypto_policy_set_rtp_default`, etc.)
2. Désassemblage ARM64 (avec `capstone` + `pyelftools`, scripts dans
   `scratchpad/libsa_analysis/`) de la fonction appelant `srtp_add_stream`
   a révélé : construction d'un buffer de 32 octets = concat de deux appels
   `sa_util_md5_hash` (une fonction maison, qui fait un simple
   `MD5(strlen-based concat des chaines)`), chacun sur les deux chaînes
   trouvées à des offsets fixes (`+0x1bc` et `+0x1dd`, inversés selon un flag
   de rôle) dans une structure de connexion.
3. Ces deux chaînes se sont avérées être exactement `device_token` et
   `client_token` du bloc `device_info` de la réponse `conn_init` (confirmé en
   capturant, via Frida, le JSON en clair juste après déchiffrement par
   `sa_util_decrypt` — ce JSON contenait littéralement ces valeurs).
4. Test empirique avec `pylibsrtp` contre un vrai paquet capturé → match exact.

## 3. Scripts clés (dans le scratchpad de session)

- `mydlink_check.py` — login cloud + liste/détails appareil (fonctionne).
- `live_view_standalone.py` — **le script principal**, 100% autonome côté
  signalisation + déchiffrement SRTP, relaie vers `ffplay` via un fichier SDP
  local + deux sockets UDP (`VIDEO_RELAY_PORT`/`AUDIO_RELAY_PORT`). Mot de
  passe demandé en interactif (`stderr`), donc à lancer soi-même dans un
  terminal, pas via un outil non-interactif.
- `libsa_analysis/` — outils de désassemblage :
  - `find_syms.py <so> <nom1> <nom2> ...` — trouve adresse+taille d'un symbole exporté.
  - `disasm.py <so> <adresse_hex> <taille>` — désassemble une plage d'adresses.
  - `find_callers.py <so> <adresse_hex>` — scanne tout `.text` pour trouver les
    instructions `BL` vers cette adresse (liste des appelants).
  - `find_containing.py <so> <adresse_hex>` — trouve le symbole qui contient
    une adresse donnée.
  - `find_func_start.py <so> <adresse_hex>` — remonte au prologue de fonction
    (`stp x29,x30,...`) le plus proche avant une adresse, pour les fonctions
    statiques non exportées.
- `test_key_derivation.py` — vérifie la formule de clé SRTP contre un vrai
  paquet capturé (`srtp_pair2.txt`).
- Scripts Frida (nécessitent Frida gadget patché dans l'APK + téléphone
  rooté-like via objection, voir §5) : `frida_srtp_hook.js` (hook propre,
  stable, capture plaintext/ciphertext de `srtp_unprotect`),
  `frida_key_hooks.js`, `frida_final_verify.js`, `frida_native_udp.js`.

## 4. Heartbeats de contrôle — **RÉSOLU — AUTONOMIE 100% FONCTIONNELLE**

`live_view_standalone.py` affiche maintenant la vidéo en direct (1280x720,
30fps, H264 + audio opus) **sans téléphone ni Frida**. Le dernier verrou était
le protocole de heartbeat sur le canal de contrôle.

### Le canal de contrôle (channel 0xFF) — format du heartbeat
La caméra envoie des heartbeats chiffrés de 25 octets (channel 0xFF) et
**refuse de streamer tant qu'elle ne reçoit pas de heartbeats VALIDES de notre
part**. Format (reverse-engineeré depuis `sa_util_create_ctrl_data` @ `0x66538`) :

```
24 FF 00 15 | type(1 octet) | counter_BE(4 octets) | MD5(counter_LE || client_token || device_token)[:16]
```
- `counter` = `rand()` uint32. Haché en **little-endian** (octets natifs ARM),
  mais écrit dans le paquet en **big-endian** (inversé). Donc dans le paquet
  le champ counter = reverse des 4 octets hachés.
- `client_token` et `device_token` = les tokens 32-hex de `device_info` de la
  réponse `conn_init` (les MÊMES que pour la clé SRTP). **Ordre dans le MD5 :
  client_token PUIS device_token.**
- Hash = MD5 complet (16 octets), PAS SHA1. (Confirmé : le buffer digest sur
  la pile fait exactement 16 octets.)
- Vérifié octet-pour-octet contre 5 paquets capturés (hook Frida de
  `sa_util_create_ctrl_data`), y compris un type=1. Match parfait.

### Machine à états des `type` (le point critique)
Il y a un petit handshake par le champ `type` :
- Le **client** (nous) commence en envoyant **type=0**, puis **escalade
  proactivement vers type=1 après les 2 premiers heartbeats** — SANS attendre
  quoi que ce soit de la caméra.
- La caméra ne passe en **type=2** (et ne commence à streamer) **qu'après
  avoir reçu notre type=1**.
- **Piège** : au départ j'avais inversé (j'attendais le type=2 de la caméra
  pour envoyer type=1) → deadlock mutuel, la caméra restait en type=0 et
  abandonnait après ~5-8 heartbeats. La bonne logique est l'escalade
  proactive côté client.

### Implémentation (dans `live_view_standalone.py`)
```python
def build_heartbeat(ptype):
    r = secrets.token_bytes(4)                      # counter little-endian (haché)
    digest = hashlib.md5(r + client_token.encode() + device_token.encode()).digest()[:16]
    return bytes([0x24,0xFF,0x00,0x15, ptype]) + r[::-1] + digest   # counter big-endian dans le paquet
```
- On ouvre un socket UDP asyncio (`DatagramProtocol`) sur notre port local.
- On envoie des heartbeats type=0 vers `device_info.local_addr:local_port` de
  la caméra, toutes les 1s + en réponse à chaque heartbeat reçu.
- Après 2 heartbeats reçus, on bascule en type=1 et on y reste.
- La caméra se met alors à envoyer la vidéo SRTP (channels 0x20-0x23) sur
  notre socket, qu'on déchiffre avec pylibsrtp (§2) et relaie vers ffplay.

### Détails qui ont aussi compté
- `websockets.connect(..., ping_interval=None)` : le relais cloud ne répond
  pas aux ping protocole WS → sinon déconnexion `keepalive ping timeout`.
- Boucle de réception UDP NON bloquante (`asyncio.DatagramProtocol`), sinon
  l'event loop ne traite plus le websocket.
- STUN réel (`stun_get_mapped_address`) pour `conn_add_candidate` (même LAN
  donc pas strictement nécessaire mais inoffensif).
- ffplay lancé avec flags basse latence (`-fflags nobuffer -flags low_delay
  -framedrop -sync ext` + `setpts=0`) sinon ça bufferise/rame.
- Les channels 0x20/0x21/0x22/0x23 = RTP vidéo / RTCP vidéo / RTP audio /
  RTCP audio (offset 0x20 sur la numérotation RTSP-interleaved standard
  0/1/2/3). Payload type RTP : 96=vidéo H264, 97=audio opus.

### Fonctions clés de libsa.so pour cette partie
- `sa_util_create_ctrl_data` @ `0x66538` : construit le heartbeat (décrit ci-dessus).
  Appelée depuis `0x5b644` avec type=`[ctx+0x114]`, tok1=`ctx+0x3c`
  (=client_token), tok2=`ctx+0x5d` (=device_token).
- Le counter vient de `rand()` (PLT @ `0x54950`).

## 5. Solution de repli qui fonctionne à 100% : téléphone + Frida

Si l'autonomie complète n'est pas débloquée, cette méthode marche
intégralement (testée, vidéo+audio fluides, ~1600 paquets vidéo sans échec
sur plusieurs dizaines de secondes) :

1. APK mydlink patché avec un gadget Frida (`objection patchapk`), installé
   sur le OnePlus Nord (nécessite : JDK, Android SDK build-tools/platform-tools,
   apktool, APKEditor pour fusionner les splits d'App Bundle — tout est déjà
   installé dans le scratchpad de session, voir `tools/android-sdk`,
   `tools/apktool.jar`, `libsa_analysis` etc. Device: OnePlus Nord AC2003,
   arm64-v8a).
2. `adb forward tcp:27042 tcp:27042`, lancer l'app, attacher avec
   `frida -H 127.0.0.1:27042 -n Gadget -l frida_srtp_hook.js -q -t inf`.
3. Ce script hook `srtp_unprotect` nativement (stable, pas de crash) et
   envoie (`send()`) chaque paquet RTP déchiffré.
4. `srtp_relay.py` (script Python utilisant le module `frida`, PAS la CLI)
   reçoit ces messages et les relaie par UDP vers `ffplay` via un SDP local
   (deux ports, un pour vidéo PT=96, un pour audio PT=97).
5. **Piège connu** : `frida -q` (CLI) quitte après le chargement du script
   sauf si on ajoute `-t inf`. Mélanger un hook Java (`Java.perform`) et un
   hook natif dans le même script cause des crashs SIGSEGV aléatoires sur ce
   téléphone — toujours les séquencer (natif d'abord, Java ensuite avec un
   `setTimeout` de quelques secondes), ou les garder séparés.
6. Le téléphone (OnePlus Nord) a eu des soucis de buffer Windows/PowerShell
   (`*>` redirection vers fichier ne flush pas tant qu'il n'y a pas assez de
   données) — toujours attendre/générer un peu d'activité avant de lire un
   fichier de log.

## 6. API cloud — autres fonctionnalités déjà confirmées exploitables

Pour construire la "couche par-dessus" (dashboard), ces endpoints
fonctionnent déjà bien au-delà du direct :
- Liste des appareils, détails, settings (`get_setting`), niveau de service
  (`get_service_level`), mise à jour firmware auto (`get_fw_auto_upgrade`),
  tout passe par le même websocket JSON cloud (`sign_in` → puis les commandes
  avec `client_id`+`device_token`+`device_id`).
- Les settings renvoient des infos détaillées : résolution/bitrate/fps,
  teinte/saturation/luminosité, flip/mirror, SSID wifi + mot de passe (!),
  fuseau horaire, etc. (voir capture `get_setting` dans les logs Frida
  antérieurs).
- Evénements : commande `sd_list_event`/`cont_evt_list_all`/`hd_list_event`
  identifiées dans le code décompilé (pas encore testées en pratique mais le
  format JSON est le même schéma que les autres commandes).

## 7. Détails techniques divers utiles

- Format `device_token` cloud : `<MAC>-<uuid-like-32-hex-avec-tirets>` (ex.
  `AABBCCDDEEFF-398f5c34-0972-07ed-7a0a-f6e671e96b1b`).
- Format `device_token`/`client_token` de session (`device_info` de
  `conn_init`) : 32 caractères hex sans tirets, changent à CHAQUE nouvelle
  connexion (`conn_init`).
- `local_cid` : entier attribué par la caméra lors d'un `sign_in` sur le canal
  **local** (`da_url`, port 8080 direct), accompagné d'un `salt` — PAS
  utilisé dans le chemin cloud de `live_view_standalone.py`. Pour ce canal
  local, le "device_token" local (format `MAC-<40 hex SHA1>`) se calcule par
  `SHA1(cloud_device_token + salt)` — formule aussi vérifiée empiriquement
  mais pour un usage différent (canal da_url local, pas utilisé dans la
  solution finale).
- Toujours forcer `ssl.CERT_NONE` pour toutes les connexions TLS locales/cloud
  (certificats auto-signés / pinning côté app officielle, mais aucune
  vérification cryptographique n'est needed pour nos propres scripts qui
  parlent directement en HTTPS/WSS standard).
