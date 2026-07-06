"""Autobuy — sniper d'éditions collector/limitées (Micromania, Fnac, Funko Europe).

Détecte un restock, ajoute au panier, pousse le checkout jusqu'au 3-D Secure
(que TU valides dans ton appli banque — obligatoire en Europe) et t'alerte
instantanément (ntfy + Discord) avec le lien de paiement prêt.

⚠️ Garde-fous : DRY_RUN activé par défaut (aucun achat réel), plafonds de prix et
de dépense, quantité 1, achat unique. Aucune donnée de carte n'est stockée : le
bot s'appuie sur tes comptes marchands (carte + adresse déjà enregistrées).
"""

__all__ = ["net", "notify", "watchlist", "monitor", "buy", "sites"]
