"""Stratégie de titre pour les produits de niche (modèle jamais recherché).

Problème : quand le NOM DU MODÈLE a un volume ~0 (ex. « J-Cut 60 Li »), le
titre doit être construit sur les MOTS GÉNÉRIQUES du type de produit
(« tondeuse cheveux », « tondeuse coiffeur ») que les acheteurs tapent
réellement — le nom exact du modèle passe en fin de titre.

Piège classique : la marque seule peut avoir un volume énorme mais ambigu
(« jaguar » = la voiture). Un générique n'est retenu pour le titre que s'il
contient le mot du type de produit.

Subtilité : le mot du type (« tondeuse ») est présent dans le nom du modèle
ET dans les génériques — il ne compte donc PAS comme mot distinctif du
modèle. Distinctifs = mots du nom SANS le type ni les mots-outils
(ex. « Tondeuse J-Cut 60 Li » -> {cut, 60, li}).
"""
from __future__ import annotations

import os

# Volume mensuel en dessous duquel un mot-clé est considéré « mort »
# (quasi personne ne le tape).
SEUIL_MORT = float(os.environ.get("SEO_SEUIL_MORT", "20"))

MOTS_OUTILS = {"de", "la", "le", "les", "des", "du", "en", "et", "avec", "pour",
               "a", "à", "au", "aux", "un", "une", "-"}

# Génériques connus par type de produit : enrichissent les candidats envoyés à
# DataForSEO (les volumes réels trancheront). Étends librement cette table.
GENERIQUES_PAR_TYPE = {
    "tondeuse": [
        "tondeuse cheveux", "tondeuse cheveux pro", "tondeuse coiffeur",
        "tondeuse sans fil", "tondeuse barbe", "tondeuse professionnelle",
        "tondeuse rechargeable",
    ],
    "casque": [
        "casque bluetooth", "casque audio", "casque sans fil",
        "casque enfant", "casque bluetooth enfant",
    ],
    "camera": [
        "camera surveillance", "camera wifi", "camera enfant",
        "appareil photo enfant", "camera sans fil",
    ],
    "coffret matcha": [
        "coffret matcha", "kit matcha", "set matcha", "matcha cérémonie",
        "bol matcha", "fouet matcha",
    ],
    "figurine": [
        "figurine", "funko pop", "figurine collection", "figurine pop",
    ],
    "console": [
        "console de jeux", "console portable", "console retro",
    ],
    "montre": [
        "montre homme", "montre femme", "montre connectée", "montre automatique",
    ],
    "sac": [
        "sac à main", "sac bandoulière", "sac cuir", "sac de voyage",
    ],
}


def _tokens(texte: str) -> set[str]:
    return {m for m in (texte or "").lower().replace("-", " ").split() if m}


def detecter_type_produit(nom: str, categorie: str = "") -> str | None:
    """Trouve le type de produit (clé de GENERIQUES_PAR_TYPE) dans le nom/catégorie."""
    texte = f"{nom} {categorie}".lower()
    for cle in sorted(GENERIQUES_PAR_TYPE, key=lambda k: -len(k)):
        if all(mot in texte for mot in cle.split()):
            return cle
    return None


def mots_distinctifs(nom: str, type_produit: str | None) -> set[str]:
    """Mots du nom qui identifient VRAIMENT le modèle (sans le type ni les mots-outils)."""
    mots = _tokens(nom)
    if type_produit:
        mots -= set(type_produit.split())
    mots -= MOTS_OUTILS
    return {m for m in mots if len(m) > 1}


def enrichir_candidats(candidats: list[str], nom: str, categorie: str = "") -> list[str]:
    """Ajoute les génériques du type détecté aux candidats (dédoublonné)."""
    type_p = detecter_type_produit(nom, categorie)
    if type_p:
        for g in GENERIQUES_PAR_TYPE[type_p]:
            if g not in candidats:
                candidats.append(g)
    return candidats


def strategie_titre(resultats: list[dict], nom: str, marque: str = "",
                    categorie: str = "") -> dict:
    """Décide la stratégie de titre à partir des volumes réels.

    resultats : liste [{'keyword':…, 'volume':…}] triée par volume décroissant.
    Renvoie {mode, type_produit, volume_modele, generiques_forts, mots_titre, pieges}.
      - mode 'modele'    : le modèle est recherché -> titre construit dessus
      - mode 'generique' : modèle mort -> titre construit sur les génériques,
                           nom exact du modèle en fin de titre
    """
    type_p = detecter_type_produit(nom, categorie)
    distinctifs = mots_distinctifs(nom, type_p)
    marque_bas = (marque or "").lower().strip()

    def parle_du_modele(kw: str) -> bool:
        return bool(_tokens(kw) & distinctifs)

    vol_modele = max(
        (r.get("volume") or 0 for r in resultats if parle_du_modele(r["keyword"])),
        default=0,
    )

    # Pièges : mot unique qui ne contient pas le type (ex. marque seule
    # ambiguë comme « jaguar ») -> jamais dans le titre.
    pieges = []
    generiques_forts = []
    mot_type = type_p.split()[0] if type_p else None
    for r in resultats:
        kw, vol = r["keyword"], r.get("volume") or 0
        if parle_du_modele(kw):
            continue
        toks = _tokens(kw)
        if len(toks) == 1 and (mot_type not in toks if mot_type else True):
            if kw not in pieges:
                pieges.append(kw)          # ex. « jaguar » seul
            continue
        if vol >= SEUIL_MORT and (mot_type is None or mot_type in toks):
            generiques_forts.append(kw)

    if vol_modele >= SEUIL_MORT:
        mode = "modele"
        mots_titre = [nom.lower()] + generiques_forts[:2]
    else:
        mode = "generique"
        mots_titre = generiques_forts[:3] + [nom.lower()]

    # La marque reste utile accolée au produit (pas seule) : on la garde dans
    # le titre si elle n'est pas déjà dans le nom.
    if marque_bas and marque_bas not in " ".join(mots_titre):
        mots_titre.append(marque_bas)

    return {
        "mode": mode,
        "type_produit": type_p,
        "mots_distinctifs": sorted(distinctifs),
        "volume_modele": vol_modele,
        "generiques_forts": generiques_forts,
        "mots_titre": mots_titre,
        "pieges": pieges,
    }
