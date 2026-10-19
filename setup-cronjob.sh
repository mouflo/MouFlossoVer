#!/bin/bash
# Installe la mise à jour automatique de nuit (idempotent).
# AJOUTE la ligne à la crontab root, ne remplace JAMAIS les lignes existantes (MouFloster, MouFlanga...).
set -e

REPO_DIR="/opt/mouflossover"
DEPLOY_SCRIPT="$REPO_DIR/deploy.sh"
CRON_LOG="/var/log/mouflossover-cron.log"

[ -d "$REPO_DIR" ] || { echo "❌ $REPO_DIR introuvable"; exit 1; }

touch "$CRON_LOG"
chmod 666 "$CRON_LOG"

CRON_ENTRY="0 5 * * * bash $DEPLOY_SCRIPT >> $CRON_LOG 2>&1"

if crontab -l 2>/dev/null | grep -qF "$DEPLOY_SCRIPT"; then
    echo "✅ La mise à jour automatique est déjà configurée"
else
    (crontab -l 2>/dev/null || true; echo "$CRON_ENTRY") | crontab -
    echo "✅ Mise à jour automatique ajoutée (chaque nuit à 5 h)"
fi

echo "Crontab actuelle :"
crontab -l
