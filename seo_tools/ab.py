"""Comparaison A/B sur stats RÉELLES (vues, favoris) — pas sur des impressions.

Principes :
- Un test A/B n'a de sens que sur de vraies stats relevées sur la plateforme.
- On ne change qu'UNE chose à la fois (le titre) ; le reste identique.
- On laisse tourner >= 48 h par variante (le boost de nouveauté fausse les
  premières heures).
- Score = (vues × POIDS_VUE + favoris × POIDS_FAVORI) / heures × 24
  -> un score « par jour », comparable entre variantes même si elles n'ont
  pas tourné le même temps. Un favori vaut 10 vues (intention d'achat).
"""
from __future__ import annotations

import os

POIDS_VUE = float(os.environ.get("AB_POIDS_VUE", "1"))
POIDS_FAVORI = float(os.environ.get("AB_POIDS_FAVORI", "10"))
HEURES_MIN_FIABLE = float(os.environ.get("AB_HEURES_MIN", "48"))


def score_jour(vues: int | None, favoris: int | None, heures: float | None) -> float:
    """Score pondéré normalisé sur 24 h. 0 si pas de durée."""
    if not heures:
        return 0.0
    brut = (vues or 0) * POIDS_VUE + (favoris or 0) * POIDS_FAVORI
    return round(brut / float(heures) * 24, 2)


def comparer(annonce: dict, stats: dict, ventes: dict) -> dict:
    """Construit le bilan A/B d'une annonce.

    stats  : {'A': {vues, favoris, heures}, 'B': {...}} (dernier relevé par variante)
    ventes : {'A': n, 'B': n}
    """
    lignes = []
    avertissements = []

    for label in ("A", "B"):
        titre = annonce.get("titre") if label == "A" else annonce.get("titre_b")
        if label == "B" and not titre:
            continue  # pas de variante B définie
        s = stats.get(label)
        if not s or s.get("vues") is None:
            avertissements.append(f"Variante {label} : pas encore de stats relevées.")
            continue
        heures = s.get("heures") or 0
        if heures < HEURES_MIN_FIABLE:
            avertissements.append(
                f"Variante {label} : seulement {heures:.0f} h en ligne — attends "
                f"{HEURES_MIN_FIABLE:.0f} h pour un résultat fiable."
            )
        lignes.append({
            "label": label,
            "titre": titre,
            "vues": s.get("vues") or 0,
            "favoris": s.get("favoris") or 0,
            "heures": heures,
            "ventes": ventes.get(label, 0),
            "score_jour": score_jour(s.get("vues"), s.get("favoris"), heures),
        })

    out = {
        "a_variante_b": bool(annonce.get("titre_b")),
        "variantes": sorted(lignes, key=lambda x: x["score_jour"], reverse=True),
        "ventes": ventes,
        "avertissements": avertissements,
        "gagnant": None,
        "conseil": None,
    }
    if len(lignes) >= 2:
        g = out["variantes"][0]
        out["gagnant"] = g["label"]
        out["conseil"] = (
            f"Garde le titre {g['label']} (score {g['score_jour']}/jour contre "
            f"{out['variantes'][1]['score_jour']}). Applique-le et arrête l'autre variante."
        )
    elif len(lignes) == 1:
        out["conseil"] = (
            f"Une seule variante mesurée ({lignes[0]['label']}). Relève aussi les stats "
            "de l'autre après l'avoir laissée tourner 48 h."
        )
    return out
