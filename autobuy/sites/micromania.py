"""Adaptateur Micromania (Salesforce Commerce Cloud + DataDome).

Micromania n'expose pas d'API produit propre : on lit la fiche HTML (le bloc
analytics embarqué donne prix + dispo, comme le fait déjà `micromania_deals.py`).
L'anti-bot DataDome est absorbé par la couche `net` (impersonation curl_cffi).

Fiabilité par ordre : détection de stock (solide) > alerte (solide) >
auto-panier SFCC (best-effort, à confirmer en live sur ton compte connecté).
"""

from __future__ import annotations

import os
import re

from .. import net
from .base import Offer

MICROMANIA_BASE = os.environ.get("MICROMANIA_BASE", "https://www.micromania.fr").rstrip("/")
# Rayons collector/figurines à surveiller (URLs de catégorie séparées par des virgules).
MICROMANIA_CATEGORIES = [
    c.strip() for c in os.environ.get("MICROMANIA_CATEGORIES", "").split(",") if c.strip()
]

_SESSION = net.Session(name="micromania", base_url=MICROMANIA_BASE,
                       min_interval=float(os.environ.get("MICROMANIA_MIN_INTERVAL", "4.0")))

_OOS = ("rupture", "indisponible", "épuisé", "epuise", "bientôt de retour",
        "bientot de retour", "non disponible", "out of stock", "sold out")
_PRICE_RE = re.compile(r'"(?:price|priceValue|productPrice)"\s*:\s*"?(\d+[.,]\d{2})')
_IMG_RE = re.compile(r'"(?:image|imageUrl|productImage)"\s*:\s*"(https?://[^"]+)"')
_LINK_RE = re.compile(r'href="(https?://www\.micromania\.fr/[^"]+?-\d+\.html)"')
_TITLE_RE = re.compile(r'<title>([^<]+)</title>', re.I)


def session() -> net.Session:
    return _SESSION


def _price(html: str) -> float | None:
    m = _PRICE_RE.search(html)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return None


def _in_stock(html: str) -> bool:
    low = html.lower()
    # Bouton d'achat présent ET aucun marqueur de rupture visible.
    has_buy = ("ajouter au panier" in low) or ('"availability"' in low and '"instock"' in low)
    oos = any(m in low for m in _OOS)
    return has_buy and not oos


def check_stock(target) -> Offer | None:
    url = getattr(target, "url", "") or ""
    if not url:
        return None
    resp = _SESSION.get(url if url.startswith("http") else "/" + url.lstrip("/"))
    if resp.challenged or resp.status != 200:
        return None
    html = resp.text
    if not _in_stock(html):
        return None
    title = (_TITLE_RE.search(html).group(1).strip() if _TITLE_RE.search(html)
             else getattr(target, "label", "") or url)
    img = _IMG_RE.search(html)
    return Offer(
        site="micromania", label=title, price=_price(html), url=url,
        available=True, image=(img.group(1) if img else None),
        checkout=url,   # le panier réel se fait via add_to_cart / handoff Playwright
    )


def scan_collector(categories: list[str] | None = None, cap: int = 40) -> list[Offer]:
    """Découvre les produits présents dans les rayons collector surveillés.

    On lit les tuiles de la page catégorie (URL + titre) ; le monitor dédoublonne,
    donc seules les NOUVEAUTÉS déclenchent une alerte. Prix/dispo précis via
    check_stock au moment voulu.
    """
    offers: list[Offer] = []
    for cat in (categories or MICROMANIA_CATEGORIES):
        resp = _SESSION.get(cat if cat.startswith("http") else "/" + cat.lstrip("/"))
        if resp.challenged or resp.status != 200:
            continue
        seen = set()
        for m in _LINK_RE.finditer(resp.text):
            link = m.group(1)
            if link in seen:
                continue
            seen.add(link)
            offers.append(Offer(site="micromania", label=link.rsplit("/", 1)[-1],
                                price=None, url=link, available=True, checkout=link))
            if len(offers) >= cap:
                break
    return offers


def checkout_url(offer: Offer) -> str:
    return offer.checkout or offer.url


def add_to_cart(offer: Offer, qty: int = 1) -> net.Session | None:
    """Best-effort SFCC : l'ajout panier + checkout Micromania passe par le flux
    Playwright connecté (voir autobuy/buy.py). En HTTP pur c'est fragile à cause du
    CSRF/DataDome, donc on renvoie None et on laisse l'orchestrateur gérer le handoff."""
    return None
