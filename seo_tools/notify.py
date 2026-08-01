"""Alertes push via ntfy (https://ntfy.sh).

ntfy est gratuit et sans compte : tu t'abonnes à un "topic" (nom secret de ton
choix) depuis l'appli mobile ntfy, et le serveur POST sur ce topic pour
recevoir une notification push.

Config .env :
    NTFY_TOPIC=mon-topic-secret-de-revente
    NTFY_SERVER=https://ntfy.sh        (par défaut ; mets ton auto-hébergé si besoin)
    NTFY_TOKEN=                        (optionnel, si topic protégé)
"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error

SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
TOPIC = os.environ.get("NTFY_TOPIC", "")
TOKEN = os.environ.get("NTFY_TOKEN", "")


def disponible() -> bool:
    return bool(TOPIC)


def envoyer(message: str, titre: str = "Revente", priorite: str = "default",
            tags: list[str] | None = None, cliquer_url: str | None = None) -> bool:
    """Envoie une notification. Renvoie True si acceptée par ntfy."""
    if not disponible():
        return False

    headers = {
        "Title": titre.encode("utf-8"),
        "Priority": priorite,
    }
    if tags:
        headers["Tags"] = ",".join(tags)
    if cliquer_url:
        headers["Click"] = cliquer_url
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"

    req = urllib.request.Request(
        f"{SERVER}/{TOPIC}",
        data=message.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return False


def alerte_bon_creneau(creneau: dict, base_url: str = "") -> bool:
    """Rappel « c'est le bon moment pour publier » (même sans annonce à republier)."""
    quand = f"{creneau.get('jour_nom', '')} {creneau.get('heure', '')}h"
    return envoyer(
        f"🕐 Bon créneau pour publier : {quand}.\n"
        "C'est le moment de poster tes nouvelles annonces (fort trafic acheteurs).",
        titre="Créneau de publication",
        priorite="high",
        tags=["alarm_clock"],
        cliquer_url=base_url or None,
    )


def alerte_republication(annonces: list[dict], base_url: str = "") -> bool:
    """Notification résumée des annonces à republier maintenant."""
    if not annonces:
        return False
    n = len(annonces)
    lignes = [f"⏰ {n} annonce(s) à republier :"]
    for a in annonces[:8]:
        emoji = "🔴" if a.get("statut_couleur") == "rouge" else "🟠"
        lignes.append(f"{emoji} {a['titre'][:45]} ({a['plateforme']})")
    if n > 8:
        lignes.append(f"… et {n - 8} autre(s)")
    return envoyer(
        "\n".join(lignes),
        titre="C'est le moment de republier",
        priorite="high",
        tags=["arrows_counterclockwise"],
        cliquer_url=base_url or None,
    )
