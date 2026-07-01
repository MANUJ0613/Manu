"""Calcul du prix conseillé, de la marge et des frais par plateforme.

Rappel des frais côté VENDEUR (mi-2026, à ajuster dans le .env si ça bouge) :

- Vinted   : 0 € de frais vendeur. C'est l'acheteur qui paie la "Protection
             acheteur" (~0,70 € + 5 %). Donc ta marge = prix de vente - prix
             d'achat. Le prix affiché doit rester attractif car l'acheteur voit
             le total protection incluse.
- Leboncoin: dépôt gratuit pour un particulier. Sur une vente avec paiement +
             livraison sécurisés, Leboncoin prélève une commission vendeur
             (~une part variable). On la modélise via LBC_COMMISSION_PCT.

Toutes les valeurs de frais sont surchargeables par variables d'environnement
pour ne pas avoir à retoucher le code quand une plateforme change ses règles.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, asdict

# --- Paramètres de frais (surchargeables) --------------------------------- #
VINTED_FRAIS_VENDEUR_PCT = float(os.environ.get("VINTED_FRAIS_VENDEUR_PCT", "0"))   # %
VINTED_FRAIS_VENDEUR_FIXE = float(os.environ.get("VINTED_FRAIS_VENDEUR_FIXE", "0"))  # €

LBC_COMMISSION_PCT = float(os.environ.get("LBC_COMMISSION_PCT", "0"))   # % vendeur si paiement sécurisé
LBC_COMMISSION_FIXE = float(os.environ.get("LBC_COMMISSION_FIXE", "0"))  # €

# Protection acheteur Vinted (informatif : payée par l'acheteur, pas par toi).
VINTED_PROTECTION_PCT = float(os.environ.get("VINTED_PROTECTION_PCT", "5"))
VINTED_PROTECTION_FIXE = float(os.environ.get("VINTED_PROTECTION_FIXE", "0.70"))


@dataclass
class Chiffrage:
    plateforme: str
    prix_achat: float
    prix_vente: float
    frais_vendeur: float          # ce que la plateforme te prélève
    net_encaisse: float           # prix_vente - frais_vendeur
    marge_euro: float             # net_encaisse - prix_achat
    marge_pct: float              # marge / prix_achat * 100
    roi_pct: float                # = marge_pct (revente : marge sur l'achat)
    cout_acheteur: float          # ce que l'acheteur paie réellement (Vinted : + protection)

    def to_dict(self) -> dict:
        return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in asdict(self).items()}


def _frais_vendeur(plateforme: str, prix_vente: float) -> float:
    p = (plateforme or "").lower()
    if p == "leboncoin":
        return prix_vente * LBC_COMMISSION_PCT / 100 + LBC_COMMISSION_FIXE
    # vinted par défaut
    return prix_vente * VINTED_FRAIS_VENDEUR_PCT / 100 + VINTED_FRAIS_VENDEUR_FIXE


def _cout_acheteur(plateforme: str, prix_vente: float) -> float:
    if (plateforme or "").lower() == "vinted":
        return prix_vente + prix_vente * VINTED_PROTECTION_PCT / 100 + VINTED_PROTECTION_FIXE
    return prix_vente


def chiffrer(prix_achat: float, prix_vente: float, plateforme: str = "vinted") -> Chiffrage:
    """Calcule la marge nette pour un prix de vente donné."""
    prix_achat = float(prix_achat or 0)
    prix_vente = float(prix_vente or 0)
    frais = _frais_vendeur(plateforme, prix_vente)
    net = prix_vente - frais
    marge = net - prix_achat
    marge_pct = (marge / prix_achat * 100) if prix_achat else 0.0
    return Chiffrage(
        plateforme=plateforme,
        prix_achat=prix_achat,
        prix_vente=prix_vente,
        frais_vendeur=frais,
        net_encaisse=net,
        marge_euro=marge,
        marge_pct=marge_pct,
        roi_pct=marge_pct,
        cout_acheteur=_cout_acheteur(plateforme, prix_vente),
    )


def prix_pour_marge(prix_achat: float, marge_cible_pct: float, plateforme: str = "vinted") -> float:
    """Prix de vente à afficher pour viser une marge nette (%) sur le prix d'achat.

    On résout : (prix_vente - frais(prix_vente)) - prix_achat = marge_cible.
    frais = prix_vente * pct/100 + fixe  ->  équation linéaire en prix_vente.
    """
    prix_achat = float(prix_achat or 0)
    cible_euro = prix_achat * marge_cible_pct / 100.0

    p = (plateforme or "").lower()
    if p == "leboncoin":
        pct, fixe = LBC_COMMISSION_PCT, LBC_COMMISSION_FIXE
    else:
        pct, fixe = VINTED_FRAIS_VENDEUR_PCT, VINTED_FRAIS_VENDEUR_FIXE

    # prix_vente*(1 - pct/100) - fixe - prix_achat = cible_euro
    denom = 1 - pct / 100.0
    if denom <= 0:
        denom = 1.0
    prix_vente = (prix_achat + cible_euro + fixe) / denom
    return round(prix_vente + 0.004, 2)  # arrondi propre


def suggestions_prix(prix_achat: float, plateforme: str = "vinted",
                     reference_marche: float | None = None) -> dict:
    """Trois paliers de prix (rapide / équilibré / marge max) + un ancrage marché.

    reference_marche : prix médian constaté (ex. eBay vendus). Sert de plafond
    réaliste ; on ne propose pas au-dessus du marché pour le palier "rapide".
    """
    paliers = {
        "vente_rapide": prix_pour_marge(prix_achat, 30, plateforme),
        "equilibre": prix_pour_marge(prix_achat, 60, plateforme),
        "marge_max": prix_pour_marge(prix_achat, 100, plateforme),
    }
    out = {"paliers": {}, "reference_marche": reference_marche}
    for nom, pv in paliers.items():
        if reference_marche and nom == "vente_rapide" and pv > reference_marche:
            pv = round(reference_marche * 0.92, 2)  # légèrement sous le marché
        out["paliers"][nom] = chiffrer(prix_achat, pv, plateforme).to_dict()
    return out
