# DCS-8000LH — client autonome + dashboard de veille

Rétro-ingénierie du protocole de vue en direct de la caméra WiFi **D-Link
DCS-8000LH** (firmware 2.07.01), aboutissant à :

1. un client Python **100 % autonome** : à partir des seuls identifiants
   mydlink du propriétaire, il se connecte au cloud, négocie la session,
   déchiffre le flux SRTP et affiche la caméra en direct — **sans
   l'application officielle, sans téléphone, sans root** ;
2. un **dashboard web local** construit par-dessus : vidéo + audio en direct,
   **détection de présence calculée localement** (différence d'images, zéro
   dépendance au cloud payant ni aux notifications D-Link), détection
   jour/nuit, captures automatiques, historique persistant, zones de
   détection et sensibilité réglables depuis l'interface.

## Contenu

- **[JOURNAL_RETRO_ENGINEERING.md](JOURNAL_RETRO_ENGINEERING.md)** — le journal
  complet : API cloud mydlink, protocole de signalisation WebSocket, dérivation
  de la clé SRTP (AES-128-CM/HMAC-SHA1-80) à partir des tokens de session,
  protocole de heartbeats de contrôle (MD5) et sa machine à états, le keepalive
  de connexion qui évite la coupure périodique, et toutes les fonctions clés de
  `libsa.so` analysées.
- **[scripts_retro/live_view_standalone.py](scripts_retro/live_view_standalone.py)**
  — le client autonome (login cloud → signalisation → heartbeats →
  déchiffrement SRTP → relais vers `ffplay`).
- **[scripts_retro/dashboard_server.py](scripts_retro/dashboard_server.py)** +
  **[scripts_retro/dashboard.html](scripts_retro/dashboard.html)** — le
  dashboard web local (réutilise `live_view_standalone`, sert
  `http://127.0.0.1:8099`). Voir [Dashboard](#dashboard-de-veille) ci-dessous.
- **scripts_retro/mydlink_check.py** — login cloud + liste/détails des appareils.
- **scripts_retro/libsa_analysis/** — outils de désassemblage ARM64 (capstone +
  pyelftools) utilisés pour analyser `libsa.so`.
- **scripts_retro/frida_*.js**, **srtp_relay.py** — variante « téléphone +
  Frida » (déchiffrement en interceptant l'app, utile pour valider).
- **scripts_retro/test_key_derivation.py** — vérifie la formule de clé SRTP
  contre un vrai paquet capturé.

## Dépendances

Python 3 + `requests`, `websockets`, `pylibsrtp`, et `ffplay`/`ffmpeg`
(FFmpeg) pour l'affichage/le dashboard. Pour le dashboard en plus : `aiohttp`,
`numpy`, `pillow`. Pour l'analyse binaire : `capstone`, `pyelftools`.

Adapter via variables d'environnement si besoin : `LOCAL_ADDR` (IP locale sur
le LAN), `FFPLAY` (chemin de `ffplay`), `FFMPEG` (chemin de `ffmpeg`, sinon
déduit de `FFPLAY` ou cherché dans le PATH).

## Dashboard de veille

```
pip install requests websockets pylibsrtp aiohttp numpy pillow
cd scripts_retro
python dashboard_server.py
```

Ouvrir ensuite `http://127.0.0.1:8099`. Le dashboard réutilise la même
connexion cloud que le client autonome, mais au lieu d'afficher avec `ffplay`,
branche `ffmpeg` pour produire un flux MJPEG + PCM servis au navigateur, et
analyse les images en local pour calculer :

- **la détection de présence** (différence d'images entre deux trames, avec
  hystérésis pour éviter les faux déclenchements), avec **zones de détection**
  dessinables sur la vidéo et une **sensibilité réglable** depuis l'interface ;
- **la détection jour/nuit** (saturation moyenne de l'image) ;
- un **journal d'événements persistant** (SQLite, survit aux redémarrages,
  pagination) avec une **capture automatique** de l'image à chaque début de
  présence détectée.

Fichiers générés localement (non suivis par git — voir `.gitignore`) :
`scripts_retro/captures/` (images), `dashboard_events.db` (historique),
`dashboard_config.json` (sensibilité + zones).

## Avertissement

Projet de recherche et d'interopérabilité mené sur **une caméra dont l'auteur
est propriétaire**, avec **ses propres identifiants**. Le code n'embarque aucun
secret d'utilisateur ; il ne contourne aucune protection d'accès d'autrui. Les
binaires du constructeur (APK) et les captures réseau personnelles ne sont
**pas** inclus dans ce dépôt. À n'utiliser que sur du matériel vous
appartenant.
