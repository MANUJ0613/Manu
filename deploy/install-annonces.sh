#!/usr/bin/env bash
# Installe l'outil de revente SEO en service systemd sur un VPS (Ubuntu/Debian).
# Usage (en root) :  bash install-annonces.sh
set -euo pipefail

DIR=/opt/annonces-seo
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

echo "==> Environnement Python + dépendances web"
python3 -m venv "$DIR/venv"
"$DIR/venv/bin/pip" install --upgrade pip >/dev/null
"$DIR/venv/bin/pip" install -r "$DIR/requirements-web.txt"

echo "==> Fichier de config /etc/annonces-seo.env"
if [ ! -f /etc/annonces-seo.env ]; then
  cp "$DIR/deploy/annonces-seo.env.example" /etc/annonces-seo.env
  chmod 600 /etc/annonces-seo.env
  echo "   -> CRÉÉ. ÉDITE-LE (clé Claude, ntfy, DataForSEO) :"
  echo "      nano /etc/annonces-seo.env"
else
  echo "   -> déjà présent, on n'écrase pas."
fi

echo "==> Service systemd"
cp "$DIR/deploy/annonces-seo.service" /etc/systemd/system/annonces-seo.service
systemctl daemon-reload
systemctl enable annonces-seo

echo ""
echo "==> Terminé."
echo "1) Remplis la config :   nano /etc/annonces-seo.env"
echo "2) Démarre :             systemctl start annonces-seo"
echo "3) Vérifie :             systemctl status annonces-seo"
echo "4) Ouvre :               http://IP_DU_VPS:8000"
echo "   (mets un reverse-proxy nginx + HTTPS devant pour la prod)"
