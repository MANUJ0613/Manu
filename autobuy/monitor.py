"""Boucle de surveillance : watchlist rapide + veille des rayons collector.

- Watchlist : on vérifie le stock de chaque cible en parallèle ; dès qu'un produit
  est dispo, on passe la main à `buy.handle` (alerte + éventuel achat auto).
- Collector : on scanne périodiquement les rayons collector/limité ; toute NOUVEAUTÉ
  déclenche une alerte de découverte (jamais d'achat auto — tu l'ajoutes à la
  watchlist en mode "auto" si tu la veux sniper).
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import buy, notify, state, watchlist
from .sites import fnac, funko, micromania

CONCURRENCY = int(os.environ.get("AUTOBUY_CONCURRENCY", "6"))
COLLECTOR_ENABLED = os.environ.get("AUTOBUY_COLLECTOR", "true").strip().lower() != "false"

# Sites activés (permet de couper Fnac/Micromania le temps de les régler).
_ENABLED = {
    "funko": os.environ.get("AUTOBUY_SITE_FUNKO", "true").strip().lower() != "false",
    "micromania": os.environ.get("AUTOBUY_SITE_MICROMANIA", "true").strip().lower() != "false",
    "fnac": os.environ.get("AUTOBUY_SITE_FNAC", "true").strip().lower() != "false",
}
_SCANNERS = {"funko": funko, "micromania": micromania, "fnac": fnac}


def enabled(site: str) -> bool:
    return _ENABLED.get(site, False)


def check_watchlist(targets: list) -> int:
    """Vérifie chaque cible active ; déclenche alerte/achat sur les dispos. Renvoie
    le nombre de produits trouvés en stock."""
    live = [t for t in targets if enabled(t.site)]
    if not live:
        return 0
    hits = 0
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        futs = {pool.submit(buy.adapter(t.site).check_stock, t): t for t in live
                if buy.adapter(t.site)}
        for fut in as_completed(futs):
            t = futs[fut]
            try:
                offer = fut.result()
            except Exception as err:  # noqa: BLE001
                print(f"[monitor] {t.key}: erreur check_stock ({err})")
                continue
            if offer and offer.available:
                hits += 1
                try:
                    buy.handle(t, offer)
                except Exception as err:  # noqa: BLE001
                    print(f"[monitor] {t.key}: erreur buy.handle ({err})")
    return hits


def scan_collectors() -> int:
    """Scanne les rayons collector des sites activés ; alerte sur les nouveautés."""
    if not COLLECTOR_ENABLED:
        return 0
    new = 0
    for site, mod in _SCANNERS.items():
        if not enabled(site) or not hasattr(mod, "scan_collector"):
            continue
        try:
            offers = mod.scan_collector()
        except Exception as err:  # noqa: BLE001
            print(f"[monitor] scan collector {site} a échoué: {err}")
            continue
        for offer in offers:
            disc_key = f"discover:{offer.site}:{offer.url}"
            if state.should_alert(disc_key):
                new += 1
                notify.alert(
                    f"🆕 Nouveauté {offer.site} : {offer.label}",
                    f"Apparue dans les rayons collector à **{offer.price_str}**.\n"
                    f"Ajoute-la à ta watchlist en mode *auto* pour la sniper.",
                    url=offer.checkout or offer.url, image=offer.image,
                    color=0x9B59B6, priority=3, tags="new")
    return new


def summary(targets: list) -> str:
    sites = ", ".join(s for s, on in _ENABLED.items() if on) or "aucun"
    autos = sum(1 for t in targets if t.auto and enabled(t.site))
    return (f"{len(targets)} cible(s) · {autos} en achat auto · sites: {sites} · "
            f"DRY_RUN={'ON' if buy.DRY_RUN else 'OFF'}")


def load_targets() -> list:
    return watchlist.load()
