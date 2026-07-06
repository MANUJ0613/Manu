"""Alertes instantanées : ntfy (push tel) + Discord (webhook) + Telegram optionnel.

Chaque alerte porte un lien direct vers le panier/paiement pour que tu finisses
en un geste (validation 3-D Secure comprise).

Config (env) :
  NTFY_URL      ex. https://ntfy.sh          (serveur ntfy)
  NTFY_TOPIC    ex. mon-autobuy-secret        (ton topic privé — garde-le secret)
  AUTOBUY_DISCORD_WEBHOOK   webhook Discord dédié (sinon DISCORD_WEBHOOK_URL)
  TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID       (optionnel)
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

NTFY_URL = os.environ.get("NTFY_URL", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
NTFY_TOKEN = os.environ.get("NTFY_TOKEN", "").strip()  # si topic protégé

DISCORD_WEBHOOK = (
    os.environ.get("AUTOBUY_DISCORD_WEBHOOK", "").strip()
    or os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

_UA = "autobuy/1.0 (+https://github.com/manuj0613/manu)"


def _post(url: str, data: bytes, headers: dict, timeout: float = 12.0) -> bool:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": _UA, **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except Exception as err:  # noqa: BLE001
        print(f"[notify] échec {url.split('/')[2]}: {err}")
        return False


def _ntfy(title: str, message: str, url: str | None, priority: int, tags: str) -> bool:
    if not NTFY_TOPIC:
        return False
    headers = {
        "Title": title.encode("utf-8"),
        "Priority": str(priority),
        "Tags": tags,
    }
    if url:
        # Bouton cliquable "PAYER" + action d'ouverture directe.
        headers["Actions"] = f"view, 💳 PAYER, {url}, clear=true"
        headers["Click"] = url
    if NTFY_TOKEN:
        headers["Authorization"] = f"Bearer {NTFY_TOKEN}"
    return _post(f"{NTFY_URL}/{NTFY_TOPIC}", message.encode("utf-8"), headers)


def _discord(title: str, message: str, url: str | None, color: int,
             image: str | None) -> bool:
    if not DISCORD_WEBHOOK:
        return False
    embed = {"title": title[:256], "description": message[:4000], "color": color}
    if url:
        embed["url"] = url
    if image:
        embed["thumbnail"] = {"url": image}
    payload = {"embeds": [embed]}
    if url:
        payload["components"] = [{
            "type": 1,
            "components": [{"type": 2, "style": 5, "label": "💳 PAYER MAINTENANT", "url": url}],
        }]
    data = json.dumps(payload).encode("utf-8")
    return _post(DISCORD_WEBHOOK, data, {"Content-Type": "application/json"})


def _telegram(title: str, message: str, url: str | None) -> bool:
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT):
        return False
    text = f"*{title}*\n{message}"
    if url:
        text += f"\n\n[💳 PAYER]({url})"
    api = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT, "text": text, "parse_mode": "Markdown",
        "disable_web_page_preview": "false",
    }).encode("utf-8")
    return _post(api, data, {"Content-Type": "application/x-www-form-urlencoded"})


def alert(title: str, message: str, *, url: str | None = None,
          image: str | None = None, priority: int = 5, color: int = 0x2ECC71,
          tags: str = "rotating_light") -> None:
    """Envoie l'alerte sur tous les canaux configurés (best-effort)."""
    sent = []
    if _ntfy(title, message, url, priority, tags):
        sent.append("ntfy")
    if _discord(title, message, url, color, image):
        sent.append("discord")
    if _telegram(title, message, url):
        sent.append("telegram")
    print(f"[notify] {title} → {', '.join(sent) or 'AUCUN CANAL configuré'}")


# Raccourcis sémantiques -----------------------------------------------------

def restock(name: str, price, url: str, image: str | None = None) -> None:
    alert(f"🟢 DISPO : {name}",
          f"Stock détecté à **{price}**.\nClique pour acheter tout de suite 👇",
          url=url, image=image, priority=5, color=0x2ECC71, tags="rotating_light")


def confirm_3ds(name: str, price, url: str) -> None:
    alert(f"💳 VALIDE LE PAIEMENT : {name}",
          f"Panier prêt à **{price}**. Le bot a poussé jusqu'au paiement — "
          f"**valide le 3-D Secure dans ton appli banque MAINTENANT.**",
          url=url, priority=5, color=0xF1C40F, tags="credit_card,warning")


def bought(name: str, price, url: str | None = None) -> None:
    alert(f"✅ COMMANDÉ : {name}",
          f"Commande passée à **{price}** 🎉", url=url, priority=4,
          color=0x2ECC71, tags="white_check_mark")
