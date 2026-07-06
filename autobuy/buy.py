"""Orchestrateur d'achat : applique les garde-fous, ajoute au panier, pousse le
checkout jusqu'au 3-D Secure, et alerte.

Ordre des sécurités (aucune n'est optionnelle) :
  1. DRY_RUN         → on simule tout SAUF passer commande (par défaut ON).
  2. mode != auto    → alerte seulement, jamais d'achat.
  3. max_price       → jamais au-dessus du plafond de l'article.
  4. plafond/jour    → AUTOBUY_DAILY_SPEND_CAP.
  5. achat unique    → on ne repousse pas 2× le même produit au checkout.

Paiement : on ne stocke jamais de carte. L'étape connectée s'appuie sur un
« storageState » Playwright (session déjà connectée que TU génères une fois), et
s'arrête à l'écran 3-D Secure — que tu valides dans ton appli banque.
"""

from __future__ import annotations

import os

from . import notify, state
from .sites import fnac, funko, micromania
from .sites.base import Offer

DRY_RUN = os.environ.get("AUTOBUY_DRY_RUN", "true").strip().lower() != "false"
USE_PLAYWRIGHT = os.environ.get("AUTOBUY_PLAYWRIGHT", "false").strip().lower() == "true"
HEADLESS = os.environ.get("AUTOBUY_HEADLESS", "true").strip().lower() != "false"

# Adaptateur par site.
ADAPTERS = {"funko": funko, "micromania": micromania, "fnac": fnac}

# storageState Playwright (session connectée) par site — chemin de fichier JSON.
_STORAGE = {
    "funko": os.environ.get("FUNKO_STORAGE", ""),
    "micromania": os.environ.get("MICROMANIA_STORAGE", ""),
    "fnac": os.environ.get("FNAC_STORAGE", ""),
}


def adapter(site: str):
    return ADAPTERS.get(site)


def _too_expensive(offer: Offer, target) -> bool:
    cap = getattr(target, "max_price", None)
    return bool(cap is not None and offer.price is not None and offer.price > cap)


def handle(target, offer: Offer) -> None:
    """Décide quoi faire d'une offre en stock : alerter et/ou acheter."""
    key = target.key

    # Toujours prévenir (throttlé) : c'est le minimum utile.
    if state.should_alert(key):
        notify.restock(offer.label, offer.price_str, offer.checkout or offer.url,
                       image=offer.image)

    # À partir d'ici : uniquement l'achat automatique.
    if not target.auto:
        return
    if _too_expensive(offer, target):
        print(f"[buy] {offer.label}: au-dessus du plafond ({offer.price} > "
              f"{target.max_price}) — alerte seule.")
        return
    if state.already_bought(key):
        return
    if offer.price is not None and not state.can_spend(offer.price):
        print(f"[buy] plafond de dépense quotidien atteint — achat suspendu.")
        return

    ad = adapter(target.site)
    # 1) Ajout panier rapide (HTTP) quand l'adaptateur le supporte (Funko/Shopify).
    if hasattr(ad, "add_to_cart"):
        try:
            ad.add_to_cart(offer)
        except Exception as err:  # noqa: BLE001
            print(f"[buy] add_to_cart {target.site} a levé: {err}")

    checkout = offer.checkout or (ad.checkout_url(offer) if hasattr(ad, "checkout_url") else offer.url)

    if DRY_RUN:
        print(f"[buy][DRY_RUN] {offer.label} — panier prêt, checkout: {checkout}")
        notify.alert(f"🧪 DRY-RUN prêt : {offer.label}",
                     f"Détecté à **{offer.price_str}**. En mode réel, le bot "
                     f"passerait au paiement ici. Aucune commande n'a été passée.",
                     url=checkout, color=0x3498DB, tags="test_tube")
        return

    # 2) Mode réel : pousser jusqu'au 3DS via la session connectée (best-effort).
    final_url = checkout
    if USE_PLAYWRIGHT and _STORAGE.get(target.site):
        try:
            ok, final_url = drive_to_3ds(target.site, checkout, _STORAGE[target.site])
            print(f"[buy] checkout piloté ({target.site}): ok={ok} url={final_url}")
        except Exception as err:  # noqa: BLE001
            print(f"[buy] pilotage checkout impossible ({err}) — handoff manuel.")
            final_url = checkout

    # 3) Handoff : tu valides le paiement / 3DS. On mémorise pour ne pas re-pousser.
    notify.confirm_3ds(offer.label, offer.price_str, final_url)
    if offer.price is not None:
        state.record_buy(key, offer.price, offer.label)


def drive_to_3ds(site: str, checkout_url: str, storage_state: str) -> tuple[bool, str]:
    """Ouvre le checkout avec une session déjà connectée (storageState Playwright)
    et laisse la page atteindre le paiement / 3DS. Retourne (ok, url_finale).

    Ne saisit AUCUNE donnée de carte : on suppose une carte enregistrée sur le
    compte. S'arrête à l'écran de paiement/3DS pour que tu confirmes.
    """
    from playwright.sync_api import sync_playwright  # import tardif (dépendance lourde)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        ctx = browser.new_context(
            storage_state=storage_state if os.path.exists(storage_state) else None,
            locale="fr-FR",
        )
        page = ctx.new_page()
        page.goto(checkout_url, wait_until="domcontentloaded", timeout=45000)
        # Tente d'avancer vers le paiement si un bouton évident est présent.
        for label in ("Payer maintenant", "Payer", "Passer la commande",
                      "Continuer vers le paiement", "Checkout", "Acheter"):
            try:
                btn = page.get_by_role("button", name=label)
                if btn and btn.is_visible():
                    btn.click(timeout=4000)
                    page.wait_for_load_state("networkidle", timeout=15000)
                    break
            except Exception:  # noqa: BLE001
                continue
        final = page.url
        # On NE valide PAS le 3DS : laissé à l'utilisateur. On ferme proprement.
        ctx.close()
        browser.close()
        return True, final
