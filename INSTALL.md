# Installation et remise en route de MouFlossover

Ce guide sert à installer MouFlossover sur le serveur (Proxmox / LXC) et à tout remettre en place après une panne ou une migration. Toutes les commandes se tapent en `root`, sans `sudo`.

## 1. Installer

```bash
cd /opt
git clone https://github.com/mouflo/MouFlossover.git mouflossover
bash /opt/mouflossover/install.sh
```

Le script `install.sh` :
- crée l'environnement Python (`venv`) et installe les dépendances ;
- installe et démarre le service `mouflossover` (port **8002**) ;
- ajoute **une ligne** à la crontab (mise à jour chaque nuit à 5 h), sans toucher aux autres lignes.

## 2. Définir l'identifiant

```bash
bash /opt/mouflossover/set-login.sh
```

Le script demande un identifiant et un mot de passe (8 caractères minimum). Seul un hash du mot de passe est conservé, dans `data/secrets.env`.

## 3. Connecter Sonarr

Ouvre `http://IP-DU-SERVEUR:8002`, clique sur ⚙️ Réglages → **Connexions** : adresse de Sonarr (ex. `http://192.168.1.133:8989/sonarr`) et clé API, puis **Tester** et **Enregistrer**. Dans **Dossiers**, vérifie le dossier des séries d'Emby (par défaut `/mnt/mouflosyno/Emby-Media/Series`).

En ligne de commande : `bash set-secret.sh SONARR_KEY "la clé"`.

## 4. Mise à jour automatique

Chaque nuit à 5 h, `deploy.sh` compare le serveur à GitHub. S'il y a du nouveau, il met le code à jour, installe les dépendances si besoin et redémarre l'appli. Si un rangement ou une analyse Wikipédia est en cours, la mise à jour attend la nuit suivante.

Après un `git push` fait depuis le serveur, redémarre le service à la main : `systemctl restart mouflossover`.

- Journal du déploiement : `tail -f /var/log/mouflossover-deploy.log`
- Journal de l'appli : `journalctl -u mouflossover -n 50`
- Tout est aussi visible dans l'appli, bouton 🩺 **Journal**

## 5. Reconstruire après une panne

1. Refaire l'étape 1 (cloner et lancer `install.sh`).
2. Remettre le dossier `data/` sauvegardé (identifiant, base Wikipédia, journaux de rangement), ou refaire les étapes 2 et 3.
3. Vérifier que le partage réseau est bien monté (`ls /mnt/mouflosyno`).

## Fichiers propres au serveur (jamais sur GitHub)

| Fichier | Contenu |
|---|---|
| `data/secrets.env` | identifiant, mot de passe haché, adresse et clé de Sonarr, dossier des séries |
| `data/mouflossover.db` | ce qui a été lu sur Wikipédia |
| `data/state.json` | crossovers mis de côté ou rangés |
| `data/manifests/` | journal de chaque rangement (pour pouvoir l'annuler) |
| `data/apparence/` | skin et couleurs choisis |
| `data/mouflossover.log` | journal de l'appli |

## Problèmes courants

- **« Sonarr ne répond pas »** : vérifie l'adresse et la clé dans ⚙️ Réglages → Connexions.
- **« Dossier introuvable »** : le partage réseau n'est pas monté, ou le chemin est faux.
- **Un crossover reste « Bloqué »** : la carte explique pourquoi (épisode pas encore téléchargé, deux fichiers pour le même épisode…).
- **L'appli ne répond plus** : `systemctl restart mouflossover`, puis ouvre le Journal.
