#!/usr/bin/env bash
# Installe le watcher Micromania en service systemd sur un VPS (Ubuntu/Debian).
# Usage (en root) :  bash install.sh
set -euo pipefail

DIR=/opt/micromania-deals
REPO="${REPO:-https://github.com/MANUJ0613/Manu.git}"
BRANCH="${BRANCH:-main}"

echo "==> Dépendances (python3, venv, git)"
apt-get update -y
apt-get install -y python3 python3-venv git

echo "==> Récupération du code dans $DIR (branche $BRANCH)"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch origin "$BRANCH"
  git -C "$DIR" checkout "$BRANCH"
  git -C "$DIR" pull --ff-only origin "$BRANCH"
else
  git clone --branch "$BRANCH" "$REPO" "$DIR"
fi
mkdir -p "$DIR/state"

echo "==> Environnement Python + curl_cffi (contournement DataDome)"
python3 -m venv "$DIR/venv"
"$DIR/venv/bin/pip" install --upgrade pip >/dev/null
"$DIR/venv/bin/pip" install -r "$DIR/requirements.txt"

echo "==> Fichier de config /etc/micromania-deals.env"
if [ ! -f /etc/micromania-deals.env ]; then
  cp "$DIR/deploy/micromania-deals.env.example" /etc/micromania-deals.env
  chmod 600 /etc/micromania-deals.env
  echo "   -> CRÉÉ. ÉDITE-LE pour mettre ton DISCORD_WEBHOOK_URL :"
  echo "      nano /etc/micromania-deals.env"
else
  echo "   -> déjà présent, on n'écrase pas."
fi

echo "==> Service systemd"
cp "$DIR/deploy/micromania-deals.service" /etc/systemd/system/micromania-deals.service
systemctl daemon-reload
systemctl enable micromania-deals

echo ""
echo "==> Terminé."
echo "1) Mets ton webhook :   nano /etc/micromania-deals.env"
echo "2) Démarre :            systemctl start micromania-deals"
echo "3) Vérifie :            systemctl status micromania-deals"
echo "4) Logs en direct :     journalctl -u micromania-deals -f"
