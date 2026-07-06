"""Adaptateur Funko (boutique Shopify — funko.com/fr par défaut).

Funko tourne sur Shopify : on exploite les endpoints publics et stables de Shopify,
bien plus rapides et fiables que le scraping HTML.

- Stock   : GET /products/<handle>.js  -> variants[].available + price (en centimes)
- Panier  : POST /cart/add.js {id, quantity}
- Checkout: permalink /cart/<variantId>:<qty>  (ajoute + saute au panier/paiement)
- Veille  : GET /collections/<handle>/products.json  (paginé)

Domaine configurable par FUNKO_BASE (ex. https://funkoeurope.com pour Funko Europe).
"""

from __future__ import annotations

import os
import re

from .. import net
from .base import Offer

FUNKO_BASE = os.environ.get("FUNKO_BASE", "https://funko.com/fr").rstrip("/")
# Rayons collector/limité à surveiller (handles de collection, séparés par virgule).
FUNKO_COLLECTIONS = [
    c.strip() for c in os.environ.get(
        "FUNKO_COLLECTIONS", "nouveautes,editions-limitees,exclusivites"
    ).split(",") if c.strip()
]

_SESSION = net.Session(name="funko", base_url=FUNKO_BASE,
                       min_interval=float(os.environ.get("FUNKO_MIN_INTERVAL", "2.0")))


def session() -> net.Session:
    return _SESSION


def _handle(target) -> str:
    """Extrait le handle produit depuis l'URL ou le champ handle."""
    if getattr(target, "handle", ""):
        return target.handle
    m = re.search(r"/products/([^/?#]+)", getattr(target, "url", "") or "")
    return m.group(1) if m else ""


def _pick_variant(variants: list[dict], wanted: str | None) -> dict | None:
    """Choisit la variante voulue (par id/titre) sinon la 1re disponible."""
    if wanted:
        w = str(wanted).lower()
        for v in variants:
            if str(v.get("id")) == w or w in (v.get("public_title") or v.get("title") or "").lower():
                return v
    for v in variants:
        if v.get("available"):
            return v
    return variants[0] if variants else None


def _product_url(handle: str) -> str:
    return f"{FUNKO_BASE}/products/{handle}"


def _offer_from(product: dict, handle: str, wanted: str | None) -> Offer | None:
    variants = product.get("variants") or []
    v = _pick_variant(variants, wanted)
    if not v:
        return None
    price = v.get("price")
    # Shopify renvoie les prix en centimes (int) sur .js, en string "xx.xx" sur .json.
    if isinstance(price, (int, float)) and price > 1000:
        price = price / 100.0
    try:
        price = float(price)
    except (TypeError, ValueError):
        price = None
    img = None
    if product.get("featured_image"):
        img = product["featured_image"]
        if isinstance(img, str) and img.startswith("//"):
            img = "https:" + img
    title = product.get("title") or handle
    vt = v.get("public_title") or v.get("title")
    label = f"{title} — {vt}" if vt and vt.lower() != "default title" else title
    vid = str(v.get("id") or "")
    return Offer(
        site="funko", label=label, price=price, url=_product_url(handle),
        available=bool(v.get("available")), variant_id=vid, image=img,
        checkout=checkout_url_for(vid),
    )


def check_stock(target) -> Offer | None:
    """Renvoie l'offre si la variante voulue est EN STOCK, sinon None."""
    handle = _handle(target)
    if not handle:
        return None
    resp = _SESSION.get(f"/products/{handle}.js")
    if resp.challenged or resp.status != 200:
        return None
    try:
        product = resp.json()
    except Exception:  # noqa: BLE001
        return None
    offer = _offer_from(product, handle, getattr(target, "variant", None))
    return offer if (offer and offer.available) else None


def scan_collector(collections: list[str] | None = None, per_page: int = 30) -> list[Offer]:
    """Liste les produits actuellement dispo dans les rayons collector surveillés."""
    offers: list[Offer] = []
    for coll in (collections or FUNKO_COLLECTIONS):
        resp = _SESSION.get(f"/collections/{coll}/products.json?limit={per_page}")
        if resp.challenged or resp.status != 200:
            continue
        try:
            products = resp.json().get("products") or []
        except Exception:  # noqa: BLE001
            continue
        for p in products:
            handle = p.get("handle") or ""
            offer = _offer_from(p, handle, None)
            if offer and offer.available:
                offers.append(offer)
    return offers


def checkout_url_for(variant_id: str, qty: int = 1) -> str:
    """Permalink Shopify : ajoute au panier ET saute au checkout."""
    if not variant_id:
        return FUNKO_BASE + "/cart"
    return f"{FUNKO_BASE}/cart/{variant_id}:{qty}"


def checkout_url(offer: Offer, qty: int = 1) -> str:
    return checkout_url_for(offer.variant_id, qty)


def add_to_cart(offer: Offer, qty: int = 1) -> net.Session | None:
    """Ajoute la variante au panier via l'API Shopify. Renvoie la session (cookies)."""
    if not offer.variant_id:
        return None
    resp = _SESSION.post("/cart/add.js",
                         json_body={"items": [{"id": int(offer.variant_id), "quantity": qty}]},
                         headers={"Accept": "application/json",
                                  "X-Requested-With": "XMLHttpRequest"})
    if resp.status in (200, 201):
        return _SESSION
    print(f"[funko] add_to_cart a échoué (status {resp.status})")
    return None
