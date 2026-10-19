#!/bin/bash
# Installation de MouFlossover sur le serveur (à lancer une fois après le clone).
set -e
REPO_DIR="/opt/mouflossover"
cd "$REPO_DIR"

chmod +x deploy.sh setup-cronjob.sh set-login.sh set-secret.sh

[ -d venv ] || python3 -m venv venv
./venv/bin/pip install -q -r requirements.txt

cp mouflossover.service /etc/systemd/system/mouflossover.service
systemctl daemon-reload
systemctl enable mouflossover
systemctl restart mouflossover

bash setup-cronjob.sh

echo "✅ Installation terminée : http://<ip-du-serveur>:8002"
echo "Prochaine étape : bash set-login.sh (identifiant), puis ⚙️ Réglages dans l'appli pour connecter Sonarr."
