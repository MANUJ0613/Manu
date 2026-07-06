"""Chargement de la watchlist des produits à guetter.

Fichier `autobuy_watchlist.json` (chemin surchargé par AUTOBUY_WATCHLIST) :

[
  {
    "site": "funko",              // funko | micromania | fnac
    "url": "https://funko.com/fr/products/xxxx",  // ou "handle": "xxxx"
    "variant": null,               // id/nom de variante (taille/édition) — optionnel
    "max_price": 40,               // n'achète JAMAIS au-dessus (garde-fou)
    "mode": "auto",                // "auto" = tente l'achat · "alert" = prévient seulement
    "label": "Pop! Batman édition limitée"
  }
]

Tout champ inconnu est ignoré. `mode` par défaut = "alert" (sécurité : on n'achète
que ce que tu as explicitement marqué "auto").
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

WATCHLIST_FILE = os.environ.get("AUTOBUY_WATCHLIST", "autobuy_watchlist.json")
VALID_SITES = {"funko", "micromania", "fnac"}


@dataclass
class Target:
    site: str
    url: str = ""
    handle: str = ""
    variant: str | None = None
    max_price: float | None = None
    mode: str = "alert"           # "auto" ou "alert"
    label: str = ""

    @property
    def key(self) -> str:
        """Identifiant stable pour la dédup (site + produit + variante)."""
        base = self.handle or self.url
        return f"{self.site}:{base}:{self.variant or '*'}"

    @property
    def auto(self) -> bool:
        return self.mode == "auto"


def _coerce(raw: dict) -> Target | None:
    site = str(raw.get("site", "")).strip().lower()
    if site not in VALID_SITES:
        print(f"[watchlist] entrée ignorée (site invalide): {raw!r}")
        return None
    url = str(raw.get("url", "")).strip()
    handle = str(raw.get("handle", "")).strip()
    if not (url or handle):
        print(f"[watchlist] entrée ignorée (ni url ni handle): {raw!r}")
        return None
    mp = raw.get("max_price")
    try:
        mp = float(mp) if mp is not None else None
    except (TypeError, ValueError):
        mp = None
    mode = str(raw.get("mode", "alert")).strip().lower()
    if mode not in ("auto", "alert"):
        mode = "alert"
    return Target(
        site=site, url=url, handle=handle,
        variant=(str(raw["variant"]) if raw.get("variant") not in (None, "") else None),
        max_price=mp, mode=mode,
        label=str(raw.get("label", "")).strip(),
    )


def load(path: str | None = None) -> list[Target]:
    path = path or WATCHLIST_FILE
    if not os.path.exists(path):
        print(f"[watchlist] fichier absent: {path} (0 cible)")
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as err:
        print(f"[watchlist] lecture impossible ({err}) — 0 cible")
        return []
    if not isinstance(data, list):
        print("[watchlist] format attendu: une liste JSON — 0 cible")
        return []
    targets = [t for t in (_coerce(r) for r in data if isinstance(r, dict)) if t]
    autos = sum(1 for t in targets if t.auto)
    print(f"[watchlist] {len(targets)} cible(s) chargée(s) ({autos} en achat auto)")
    return targets
