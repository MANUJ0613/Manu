"""Structures partagées par les adaptateurs de site."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Offer:
    """Une offre concrète : un produit disponible, prêt à être mis au panier."""

    site: str
    label: str
    price: float | None            # en euros
    url: str                       # page produit
    available: bool
    variant_id: str = ""           # identifiant de variante (pour panier/checkout)
    image: str | None = None
    checkout: str = ""             # lien direct panier→paiement (si dispo)

    @property
    def price_str(self) -> str:
        return f"{self.price:.2f} €".replace(".", ",") if self.price is not None else "?"
