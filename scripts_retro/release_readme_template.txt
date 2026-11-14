DCS-8000LH — Dashboard de veille (version autonome)
=====================================================

Tableau de bord local pour la caméra D-Link DCS-8000LH : vidéo + audio en
direct, détection de présence et jour/nuit calculées localement (zéro
dépendance au cloud payant D-Link). Tout est inclus dans ce dossier,
aucune installation de Python ni de FFmpeg n'est nécessaire.

DEMARRAGE RAPIDE
-----------------
1. Double-cliquer "DCS8000LH-Dashboard.exe". Une fenêtre noire (console)
   s'ouvre et affiche la progression de connexion.

2. Ouvrir un navigateur à l'adresse : http://127.0.0.1:8099

3. Un écran de connexion s'affiche (si c'est la première fois) : entrer
   l'email et le mot de passe du compte mydlink (celui de l'app D-Link),
   cocher "Se souvenir sur cet appareil", valider.

   (Les identifiants restent uniquement sur cet ordinateur, dans un
   fichier "camera_config.json" écrit à côté de l'exe. Ils ne sont
   envoyés nulle part sauf au service de connexion mydlink lui-même,
   comme le ferait l'application officielle. Il est aussi possible de
   créer ce fichier à la main en copiant "camera_config.example.json".)

Pour arrêter : fermer la fenêtre de la console, ou Ctrl+C dedans.

INSTALLER SUR TELEPHONE (PWA)
-------------------------------
Le dashboard est accessible depuis un téléphone sur le même réseau WiFi :
l'adresse à utiliser ("http://<IP>:8099") s'affiche dans la console au
démarrage. L'ouvrir dans le navigateur du téléphone, puis "Ajouter à
l'écran d'accueil" (Android : menu ⋮ dans Chrome ; iPhone : bouton
Partager dans Safari) pour une icône plein écran, comme une vraie app.

Pour y accéder aussi hors du domicile, voir la section "Accès depuis le
téléphone (PWA)" du README du dépôt (lien plus bas) — ça passe par un VPN
privé (Tailscale), pas par une ouverture de port sur internet.

DEPANNAGE
---------
- L'écran de connexion réaffiche une erreur : vérifier l'email et le mot
  de passe (ceux du compte mydlink, pas du WiFi), et la connexion internet.
- "aucun flux apres 10s" : une autre session est peut-être déjà ouverte
  sur la caméra (app officielle encore ouverte sur un téléphone, par
  exemple) — la caméra n'accepte qu'un flux à la fois.
- Pare-feu Windows : à la première ouverture, autoriser l'accès réseau
  si Windows le demande (nécessaire pour recevoir le flux de la caméra
  et servir la page web locale).
- Le téléphone n'arrive pas à joindre l'adresse affichée : c'est
  généralement le pare-feu Windows qui bloque les connexions entrantes
  d'autres appareils — accepter la boîte de dialogue Windows la première
  fois, ou l'autoriser à la main (Pare-feu Windows Defender → Paramètres
  avancés → Règles de trafic entrant → autoriser le port 8099 TCP, profil
  Privé).

A PROPOS DE CE PAQUET
----------------------
Ce dossier contient FFmpeg (ffmpeg.exe), utilisé pour décoder le flux de
la caméra. Voir FFMPEG_LICENSE.txt pour sa licence (GPL) ; binaire
construit par gyan.dev (https://www.gyan.dev/ffmpeg/builds/), à partir
des sources publiques de FFmpeg (https://ffmpeg.org). Le reste du code
(dashboard, protocole caméra) est documenté sur le dépôt du projet :
https://github.com/shalee-pnt/dcs8000lh-standalone-client
