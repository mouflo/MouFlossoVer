#!/bin/bash
# Déploiement automatique de MouFlossover : lancé chaque nuit à 5 h par cron (voir setup-cronjob.sh).
# Si GitHub a du nouveau : récupère le code, met à jour les dépendances si besoin, redémarre l'appli.

REPO_DIR="/opt/mouflossover"
LOG_FILE="/var/log/mouflossover-deploy.log"
SERVICE_NAME="mouflossover"

log() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"; }
touch "$LOG_FILE"

cd "$REPO_DIR" || { log "❌ $REPO_DIR introuvable"; exit 1; }
OLD_COMMIT=$(git rev-parse HEAD)
git fetch origin -q || { log "⚠️ GitHub injoignable, nouvel essai à la prochaine exécution"; exit 0; }
NEW_COMMIT=$(git rev-parse origin/main 2>/dev/null || git rev-parse origin/master)

if [ "$OLD_COMMIT" = "$NEW_COMMIT" ]; then
    exit 0
fi

# Un rangement ou une analyse Wikipédia est en cours : on attend la prochaine exécution
OCCUPE=$(curl -s -m 5 http://127.0.0.1:8002/api/occupe 2>/dev/null)
if echo "$OCCUPE" | grep -q '"occupe": *true'; then
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] ⏸ Mise à jour reportée : $(echo "$OCCUPE" | sed -E 's/.*"raisons": *\[([^]]*)\].*/\1/')" >> "$LOG_FILE"
    exit 0
fi

log "=== 🚀 Déploiement MouFlossover détecté ==="
log "Ancien commit: $OLD_COMMIT"
log "Nouveau commit: $NEW_COMMIT"
git reset --hard origin/main -q || git reset --hard origin/master -q

# Dépendances : seulement si requirements.txt a changé
if git diff "$OLD_COMMIT" HEAD -- requirements.txt | grep -q .; then
    log "📦 Installation des dépendances (requirements.txt modifié)..."
    if [ -d "./venv" ]; then
        ./venv/bin/python -m pip install -q -r requirements.txt 2>&1 | tee -a "$LOG_FILE"
    else
        log "⚠️ Dossier venv introuvable : dépendances non installées"
    fi
fi

# Service systemd : le fichier du dépôt est la source de vérité
if ! cmp -s "$REPO_DIR/mouflossover.service" /etc/systemd/system/mouflossover.service; then
    cp "$REPO_DIR/mouflossover.service" /etc/systemd/system/mouflossover.service
    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME" >/dev/null 2>&1
    log "📋 Service systemd mis à jour"
fi

log "🔄 Redémarrage du service..."
if systemctl is-active --quiet "$SERVICE_NAME"; then
    systemctl restart "$SERVICE_NAME"
else
    systemctl start "$SERVICE_NAME"
fi
sleep 2
if systemctl is-active --quiet "$SERVICE_NAME"; then
    log "✅ Déploiement réussi ! Service redémarré."
else
    log "❌ Erreur : le service n'a pas pu redémarrer (voir : journalctl -u $SERVICE_NAME -n 50)"
    exit 1
fi
log "=== Fin du déploiement MouFlossover ==="
