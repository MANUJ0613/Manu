"""Adaptateur Fnac (anti-bot lourd + login obligatoire au checkout).

Fnac est le plus hostile à l'automatisation : DataDome agressif, connexion requise,
distinction Fnac / Marketplace. On vise donc le solide : **détection de stock +
alerte instantanée** avec lien direct. L'achat automatique passe par le flux
Playwright connecté (best-effort) ; sinon repli alerte-seule.
"""

from __future__ import annotations

import os
import re

from .. import net
from .base import Offer

FNAC_BASE = os.environ.get("FNAC_BASE", "https://www.fnac.com").rstrip("/")
FNAC_CATEGORIES = [
    c.strip() for c in os.environ.get("FNAC_CATEGORIES", "").split(",") if c.strip()
]

_SESSION = net.Session(name="fnac", base_url=FNAC_BASE,
                       min_interval=float(os.environ.get("FNAC_MIN_INTERVAL", "5.0")))

_OOS = ("bientôt disponible", "bientot disponible", "indisponible",
        "actuellement indisponible", "rupture", "non disponible", "épuisé")
_BUY = ("ajouter au panier", "add-to-cart", '"availability":"instock"', "en stock")
_PRICE_RE = re.compile(r'"price"\s*:\s*"?(\d+[.,]\d{2})')
_TITLE_RE = re.compile(r'<title>([^<]+)</title>', re.I)
_LINK_RE = re.compile(r'href="(https?://www\.fnac\.com/[^"]+/a\d+)"')


def session() -> net.Session:
    return _SESSION


def check_stock(target) -> Offer | None:
    url = getattr(target, "url", "") or ""
    if not url:
        return None
    resp = _SESSION.get(url if url.startswith("http") else "/" + url.lstrip("/"))
    if resp.challenged or resp.status != 200:
        return None
    low = resp.text.lower()
    if any(m in low for m in _OOS) or not any(b in low for b in _BUY):
        return None
    m = _PRICE_RE.search(resp.text)
    price = float(m.group(1).replace(",", ".")) if m else None
    title = (_TITLE_RE.search(resp.text).group(1).strip()
             if _TITLE_RE.search(resp.text) else getattr(target, "label", "") or url)
    return Offer(site="fnac", label=title, price=price, url=url, available=True,
                 checkout=url)


def scan_collector(categories: list[str] | None = None, cap: int = 40) -> list[Offer]:
    offers: list[Offer] = []
    for cat in (categories or FNAC_CATEGORIES):
        resp = _SESSION.get(cat if cat.startswith("http") else "/" + cat.lstrip("/"))
        if resp.challenged or resp.status != 200:
            continue
        seen = set()
        for m in _LINK_RE.finditer(resp.text):
            link = m.group(1)
            if link in seen:
                continue
            seen.add(link)
            offers.append(Offer(site="fnac", label=link.rsplit("/", 1)[-1],
                                price=None, url=link, available=True, checkout=link))
            if len(offers) >= cap:
                break
    return offers


def checkout_url(offer: Offer) -> str:
    return offer.checkout or offer.url


def add_to_cart(offer: Offer, qty: int = 1) -> net.Session | None:
    """Fnac : panier + checkout uniquement via Playwright connecté (buy.py)."""
    return None
