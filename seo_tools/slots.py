"""Statuts de republication (🟢🟠🔴) et détection de tes meilleurs créneaux.

Deux fonctions clés :

1. statut_annonce()   : à partir de la date de dernière (re)publication, renvoie
   une couleur de fraîcheur. Sur Vinted/Leboncoin, une annonce perd en
   visibilité avec le temps ; il faut la "remonter"/republier régulièrement.

     🟢 vert   : fraîche, rien à faire
     🟠 orange : commence à dater, à republier bientôt
     🔴 rouge  : trop ancienne, republie maintenant

   Seuils réglables via REPUB_WARN_HEURES / REPUB_CRIT_HEURES.

2. meilleurs_creneaux() : analyse tes ventes passées (heure + jour de semaine)
   pour trouver quand tu vends le mieux. Repli sur des créneaux "grand public"
   Vinted/Leboncoin si tu n'as pas encore assez de données.
"""
from __future__ import annotations

import os
import time
from collections import Counter
from datetime import datetime

WARN_HEURES = float(os.environ.get("REPUB_WARN_HEURES", "72"))    # 3 j -> orange
CRIT_HEURES = float(os.environ.get("REPUB_CRIT_HEURES", "168"))   # 7 j -> rouge

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]

# Créneaux par défaut = meilleurs créneaux France (guide Vinted 2026), utilisés
# tant qu'on n'a pas assez de ventes pour personnaliser.
# (jour 0=lundi..6=dimanche, heure). Sources convergentes : mer/jeu 18-20h,
# dimanche soir 18-22h, samedi 9-11h, lundi/mardi soir 20-22h.
CRENEAUX_DEFAUT = [
    (2, 19),  # mercredi ~18h30-19h : meilleur créneau régulier
    (6, 20),  # dimanche soir : trafic +25 %
    (3, 19),  # jeudi soir
    (5, 10),  # samedi 9-11h
    (1, 21),  # mardi soir (retour de week-end)
]

# Anti-spam : Vinted détecte les doublons ; au-delà de 1 relist/jour et surtout
# au 2e-3e repost identique, la visibilité chute de 40-60 %.
RELIST_MIN_HEURES = float(os.environ.get("RELIST_MIN_HEURES", "24"))


def statut_annonce(annonce: dict, maintenant: float | None = None) -> dict:
    """Ajoute couleur + âge à une annonce (dict issu de db)."""
    maintenant = maintenant or time.time()
    ref = annonce.get("date_republication") or annonce.get("date_publication") or maintenant
    age_h = max(0.0, (maintenant - ref) / 3600.0)

    if age_h >= CRIT_HEURES:
        couleur, emoji, label = "rouge", "🔴", "À republier maintenant"
    elif age_h >= WARN_HEURES:
        couleur, emoji, label = "orange", "🟠", "À republier bientôt"
    else:
        couleur, emoji, label = "vert", "🟢", "Fraîche"

    out = dict(annonce)
    out["statut_couleur"] = couleur
    out["statut_emoji"] = emoji
    out["statut_label"] = label
    out["age_heures"] = round(age_h, 1)
    out["age_jours"] = round(age_h / 24.0, 1)
    return out


def annoter(annonces: list[dict], maintenant: float | None = None) -> list[dict]:
    maintenant = maintenant or time.time()
    return [statut_annonce(a, maintenant) for a in annonces]


def a_republier(annonces: list[dict], inclure_orange: bool = True,
                maintenant: float | None = None) -> list[dict]:
    """Filtre les annonces qui méritent une alerte (rouge, + orange en option)."""
    cibles = {"rouge"} | ({"orange"} if inclure_orange else set())
    return [a for a in annoter(annonces, maintenant) if a["statut_couleur"] in cibles]


def meilleurs_creneaux(ventes: list[dict], top: int = 5, min_ventes: int = 8) -> dict:
    """Détecte tes meilleurs créneaux (jour, heure) à partir des ventes.

    Renvoie {'source': 'stats'|'defaut', 'creneaux': [{jour, jour_nom, heure,
    ventes, part_pct}], 'total_ventes': n}.
    """
    horodatages = [v["date_vente"] for v in ventes if v.get("date_vente")]
    total = len(horodatages)

    if total < min_ventes:
        creneaux = [
            {"jour": j, "jour_nom": JOURS[j], "heure": h, "ventes": 0, "part_pct": 0.0}
            for j, h in CRENEAUX_DEFAUT[:top]
        ]
        return {"source": "defaut", "creneaux": creneaux, "total_ventes": total}

    compteur: Counter = Counter()
    for ts in horodatages:
        d = datetime.fromtimestamp(ts)
        compteur[(d.weekday(), d.hour)] += 1

    creneaux = []
    for (jour, heure), n in compteur.most_common(top):
        creneaux.append({
            "jour": jour,
            "jour_nom": JOURS[jour],
            "heure": heure,
            "ventes": n,
            "part_pct": round(n / total * 100, 1),
        })
    return {"source": "stats", "creneaux": creneaux, "total_ventes": total}


def est_bon_creneau(creneaux: list[dict], maintenant: datetime | None = None,
                    tolerance_h: int = 1) -> bool:
    """Vrai si l'instant présent tombe dans un des meilleurs créneaux (± tolérance)."""
    maintenant = maintenant or datetime.now()
    for c in creneaux:
        if c["jour"] == maintenant.weekday() and abs(c["heure"] - maintenant.hour) <= tolerance_h:
            return True
    return False


def republication_trop_recente(annonce: dict, maintenant: float | None = None) -> bool:
    """Vrai si l'annonce a déjà été republiée il y a moins de RELIST_MIN_HEURES.

    Sert de garde-fou anti-spam : republier plusieurs fois par jour la même fiche
    fait chuter la visibilité (détection de doublons Vinted).
    """
    maintenant = maintenant or time.time()
    ref = annonce.get("date_republication") or 0
    return (maintenant - ref) < RELIST_MIN_HEURES * 3600
