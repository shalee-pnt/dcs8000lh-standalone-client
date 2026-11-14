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

## Télécharger le dashboard tout-en-un (Windows)

Pas besoin d'installer Python ni FFmpeg : **[Releases](../../releases/latest)**
propose un `.zip` avec l'exécutable du dashboard déjà empaqueté (FFmpeg
inclus). Télécharger, dézipper, copier `camera_config.example.json` en
`camera_config.json` (y mettre ses identifiants mydlink), double-cliquer
`DCS8000LH-Dashboard.exe`, ouvrir `http://127.0.0.1:8099`. Voir le
`README.txt` du zip pour le détail.

Pour tout le reste (client autonome, scripts d'analyse, lancer depuis les
sources) : voir [Installation](#installation) ci-dessous.

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
- **[requirements.txt](requirements.txt)** — dépendances Python.
- **[start_dashboard.bat](start_dashboard.bat)** — lanceur Windows en un clic
  (avec relance automatique).
- **scripts_retro/camera_config.example.json** — modèle du fichier
  d'identifiants local (à copier en `camera_config.json`).

## Installation

Prérequis : **Python 3.10+** et **FFmpeg** (fournit `ffplay` et `ffmpeg`)
installés et si possible dans le `PATH`.

```
git clone https://github.com/shalee-pnt/dcs8000lh-standalone-client.git
cd dcs8000lh-standalone-client
python -m venv venv
venv\Scripts\activate          # (Linux/macOS : source venv/bin/activate)
pip install -r requirements.txt
```

**Identifiants mydlink** — le dashboard affiche un écran de connexion
(email + mot de passe) dès qu'il démarre sans identifiants connus. Les
renseigner une fois là, cocher « Se souvenir sur cet appareil », et c'est
fait : le dashboard écrit lui-même `camera_config.json` pour les lancements
suivants — pas besoin d'éditer de fichier à la main.

Pour les régler autrement (avant le premier lancement, en ligne de
commande, etc.), même fichier, copié et édité directement :

```
cd scripts_retro
copy camera_config.example.json camera_config.json
# puis editer camera_config.json : email + mot de passe du compte mydlink
```

Ordre de priorité des identifiants : variables d'environnement
`MYDLINK_EMAIL`/`MYDLINK_PASSWORD` → `camera_config.json` (à la main ou
écrit par l'écran de connexion) → formulaire web du dashboard, ou saisie
interactive pour le client autonome en ligne de commande. L'IP locale
(`LOCAL_ADDR`) est
**auto-détectée** ; la définir en variable d'environnement seulement si la
détection choisit la mauvaise interface réseau (plusieurs cartes réseau
actives, VPN, etc.). Variables disponibles : `LOCAL_ADDR`, `FFPLAY` (chemin
de `ffplay`), `FFMPEG` (chemin de `ffmpeg`, sinon déduit de `FFPLAY` ou
cherché dans le `PATH`).

### Lancer le dashboard

- **Windows, en un clic** : double-cliquer [start_dashboard.bat](start_dashboard.bat)
  à la racine du dépôt (relance automatiquement le serveur s'il s'arrête,
  par exemple après une coupure réseau prolongée).
- **En ligne de commande** (tous OS) :
  ```
  cd scripts_retro
  python dashboard_server.py
  ```

Ouvrir ensuite `http://127.0.0.1:8099`.

### Lancer le client autonome (sans dashboard)

```
cd scripts_retro
python live_view_standalone.py
```

## Dashboard de veille

Le dashboard réutilise la même connexion cloud que le client autonome, mais
au lieu d'afficher avec `ffplay`, branche `ffmpeg` pour produire un flux
MJPEG + PCM servis au navigateur, et analyse les images en local pour
calculer :

- **la détection de présence** (différence d'images entre deux trames, avec
  hystérésis pour éviter les faux déclenchements), avec **zones de détection**
  dessinables sur la vidéo et une **sensibilité réglable** depuis l'interface ;
- **la détection jour/nuit** (saturation moyenne de l'image) ;
- un **journal d'événements persistant** (SQLite, survit aux redémarrages,
  pagination) avec une **capture automatique** de l'image à chaque début de
  présence détectée.

Fichiers générés localement (non suivis par git — voir `.gitignore`) :
`scripts_retro/captures/` (images), `dashboard_events.db` (historique),
`dashboard_config.json` (sensibilité + zones), `camera_config.json`
(identifiants).

### Robustesse réseau

- Les appels au cloud mydlink (login, liste d'appareils) réessaient
  automatiquement avec un délai croissant (jusqu'à 5 tentatives) sur les
  erreurs réseau transitoires, avant d'abandonner avec un message clair.
- Le dashboard reconnecte automatiquement le flux après une coupure, avec un
  délai croissant (2s → 60s max) en cas d'échecs répétés, et **rafraîchit la
  session cloud** après 3 échecs d'affilée (utile si le jeton d'accès a
  expiré sur une instance qui tourne plusieurs jours).
- `start_dashboard.bat` relance tout le processus si jamais il s'arrête
  malgré ça (par exemple après une très longue coupure internet).

### Démarrage automatique (Windows)

Pour que le dashboard démarre avec la session Windows : créer un raccourci
vers `start_dashboard.bat` et le placer dans le dossier Démarrage
(`Win+R` → `shell:startup`). Pour un service plus robuste (redémarre même
si personne n'est connecté), utiliser le Planificateur de tâches Windows
avec un déclencheur « au démarrage de l'ordinateur ».

## Dépannage

- **`[ERREUR] Python introuvable`** (lanceur `.bat`) : installer Python 3.10+
  et cocher « Add python.exe to PATH » pendant l'installation, ou créer le
  venv (`python -m venv venv` à la racine du dépôt) pour que le lanceur le
  détecte automatiquement.
- **`ModuleNotFoundError: No module named 'numpy'`** (ou `aiohttp`, etc.) :
  le lanceur utilise le Python système faute de venv à la racine du dépôt.
  Créer le venv et y installer les dépendances (voir
  [Installation](#installation) : `python -m venv venv` puis
  `venv\Scripts\python -m pip install -r requirements.txt`) — le lanceur le
  détectera automatiquement au prochain lancement.
- **`ffplay`/`ffmpeg` introuvable** : installer FFmpeg et l'ajouter au `PATH`,
  ou définir les variables d'environnement `FFPLAY`/`FFMPEG` avec le chemin
  complet des exécutables.
- **`ECHEC CONNEXION (identifiants incorrects ?)`** : vérifier
  `camera_config.json` (ou `MYDLINK_EMAIL`/`MYDLINK_PASSWORD`) — ce sont bien
  les identifiants du compte **mydlink** (l'app), pas ceux du WiFi.
- **`Erreur reseau (...), nouvelle tentative dans Xs...`** répété en boucle :
  pas de connexion internet au moment du lancement. Le programme réessaie
  tout seul (jusqu'à 5 tentatives, délai croissant) ; `start_dashboard.bat`
  relance ensuite tout le processus si besoin.
- **Rien ne s'affiche / `aucun flux apres 10s`** : une autre session est
  peut-être déjà ouverte sur la caméra (app officielle encore ouverte sur un
  téléphone, par exemple) — la DCS-8000LH n'accepte qu'un flux à la fois.
- **La vidéo se fige puis reprend brièvement** : normal lors d'une
  renégociation (toutes les quelques dizaines de minutes selon le réseau) ;
  voir [JOURNAL_RETRO_ENGINEERING.md](JOURNAL_RETRO_ENGINEERING.md) §4bis.

## Avertissement

Projet de recherche et d'interopérabilité mené sur **une caméra dont l'auteur
est propriétaire**, avec **ses propres identifiants**. Le code n'embarque aucun
secret d'utilisateur ; il ne contourne aucune protection d'accès d'autrui. Les
binaires du constructeur (APK) et les captures réseau personnelles ne sont
**pas** inclus dans ce dépôt. À n'utiliser que sur du matériel vous
appartenant.
