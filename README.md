# DCS-8000LH — client de vue en direct autonome

Rétro-ingénierie du protocole de vue en direct de la caméra WiFi **D-Link
DCS-8000LH** (firmware 2.07.01), aboutissant à un client Python **100 %
autonome** : à partir des seuls identifiants mydlink du propriétaire, il se
connecte au cloud, négocie la session, déchiffre le flux SRTP et affiche la
caméra en direct — **sans l'application officielle, sans téléphone, sans
root**.

## Contenu

- **[JOURNAL_RETRO_ENGINEERING.md](JOURNAL_RETRO_ENGINEERING.md)** — le journal
  complet : API cloud mydlink, protocole de signalisation WebSocket, dérivation
  de la clé SRTP (AES-128-CM/HMAC-SHA1-80) à partir des tokens de session,
  protocole de heartbeats de contrôle (MD5) et sa machine à états, et toutes
  les fonctions clés de `libsa.so` analysées.
- **[scripts_retro/live_view_standalone.py](scripts_retro/live_view_standalone.py)**
  — le client autonome (login cloud → signalisation → heartbeats →
  déchiffrement SRTP → relais vers `ffplay`).
- **scripts_retro/mydlink_check.py** — login cloud + liste/détails des appareils.
- **scripts_retro/libsa_analysis/** — outils de désassemblage ARM64 (capstone +
  pyelftools) utilisés pour analyser `libsa.so`.
- **scripts_retro/frida_*.js**, **srtp_relay.py** — variante « téléphone +
  Frida » (déchiffrement en interceptant l'app, utile pour valider).
- **scripts_retro/test_key_derivation.py** — vérifie la formule de clé SRTP
  contre un vrai paquet capturé.

## Dépendances

Python 3 + `requests`, `websockets`, `pylibsrtp`, et `ffplay` (FFmpeg) pour
l'affichage. Pour l'analyse binaire : `capstone`, `pyelftools`. Adapter les
chemins en tête de `live_view_standalone.py` (chemin `ffplay`, IP locale).

## Avertissement

Projet de recherche et d'interopérabilité mené sur **une caméra dont l'auteur
est propriétaire**, avec **ses propres identifiants**. Le code n'embarque aucun
secret d'utilisateur ; il ne contourne aucune protection d'accès d'autrui. Les
binaires du constructeur (APK) et les captures réseau personnelles ne sont
**pas** inclus dans ce dépôt. À n'utiliser que sur du matériel vous
appartenant.
