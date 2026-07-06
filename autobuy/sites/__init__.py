"""Adaptateurs par site. Chacun expose la même interface :

    check_stock(target)  -> Offer | None     # None = indispo / introuvable
    scan_collector()     -> list[Offer]       # nouveautés des rayons collector
    add_to_cart(offer)   -> Session | None    # session avec le panier rempli
    checkout_url(offer)  -> str               # lien direct vers le paiement
    buy_to_3ds(target, offer) -> str          # (optionnel) pilote jusqu'au 3DS

Voir `autobuy/sites/base.py` pour la structure `Offer`.
"""
