"""Génération de l'annonce (titre + description) via l'API Claude.

Le prompt applique les bonnes pratiques SEO Vinted / Leboncoin 2026 :
- Vinted est un MOTEUR DE RECHERCHE par mots-clés : le titre et la description
  pèsent beaucoup plus que les hashtags (non cliquables, comptés comme du texte).
- Titre 50-60 caractères : marque + type + détail distinctif, mots-clés en tête,
  jamais de MAJUSCULES criardes ni « URGENT/PROMO !!! ».
- Description 80-150 mots, en 3 blocs (accroche / détails techniques / conditions),
  mots-clés longue traîne + synonymes (baskets/sneakers…), en langage naturel
  (les annonces Vinted sont indexées par Google depuis 2024).
- 3 à 5 hashtags MAX, uniquement en toute fin de description.
- Remplir TOUS les attributs (marque, taille, couleur, matière, état) : facteur
  direct de visibilité dans les filtres.

Sortie JSON structurée :
- titre_court     : ≤ 50 caractères (limite du titre Leboncoin)
- titre_vinted    : 50-60 caractères, optimisé recherche Vinted
- description     : 80-150 mots, 3 blocs, hashtags à la fin
- hashtags        : 3 à 5 (sans le #, ajouté côté UI)
- mots_cles_places: mots-clés SEO longue traîne réellement intégrés
- attributs       : valeurs à recopier dans les champs Vinted/Leboncoin

Le modèle par défaut est claude-opus-4-8 (surchargeable via ANNONCES_MODEL).
"""
from __future__ import annotations

import json
import os

try:
    import anthropic
except ImportError:  # le module Flask peut tourner même si anthropic manque
    anthropic = None  # type: ignore

MODEL = os.environ.get("ANNONCES_MODEL", "claude-opus-4-8")

# Schéma de sortie structurée : garantit un JSON exploitable directement.
SCHEMA = {
    "type": "object",
    "properties": {
        "titre_court": {"type": "string", "description": "Titre <= 50 caractères pour Leboncoin"},
        "titre_vinted": {"type": "string", "description": "Titre 50-60 car. : marque + type + détail distinctif, mots-clés en tête"},
        "description": {"type": "string", "description": "80-150 mots, 3 blocs, langage naturel, 3-5 hashtags à la toute fin"},
        "hashtags": {"type": "array", "items": {"type": "string"}, "description": "3 à 5 hashtags ciblés, sans le #"},
        "mots_cles_places": {"type": "array", "items": {"type": "string"}},
        "attributs": {
            "type": "object",
            "description": "Valeurs à recopier dans les champs Vinted/Leboncoin (chaîne vide si inconnu)",
            "properties": {
                "marque": {"type": "string"},
                "taille": {"type": "string"},
                "couleur": {"type": "string"},
                "matiere": {"type": "string"},
                "etat": {"type": "string"},
                "categorie_precise": {"type": "string"},
            },
            "required": ["marque", "taille", "couleur", "matiere", "etat", "categorie_precise"],
            "additionalProperties": False,
        },
    },
    "required": ["titre_court", "titre_vinted", "description", "hashtags", "mots_cles_places", "attributs"],
    "additionalProperties": False,
}

SYSTEME = (
    "Tu es un expert de la revente d'occasion sur Vinted et Leboncoin en France. "
    "Tu connais l'algorithme 2026 : Vinted est un MOTEUR DE RECHERCHE par mots-clés, "
    "donc le titre et la description pèsent bien plus que les hashtags (qui ne sont "
    "ni cliquables ni indexés, comptés comme du texte ordinaire).\n\n"
    "Règles impératives :\n"
    "- titre_vinted : 50 à 60 caractères, structure « marque + type + détail distinctif "
    "(taille/couleur/état) », les mots-clés les plus recherchés EN TÊTE. Interdits : "
    "MAJUSCULES criardes, « URGENT », « PROMO !!! », ponctuation spammée, mots génériques seuls.\n"
    "- titre_court : version <= 50 caractères pour Leboncoin.\n"
    "- description : 80 à 150 mots, en 3 blocs — (1) accroche 1-2 phrases sur ce qui rend "
    "l'article spécial, (2) détails techniques (marque, dimensions/mesures, couleur exacte, "
    "matière, état précis), (3) conditions (envoi rapide, lots, négociation). "
    "Utilise des mots-clés LONGUE TRAÎNE (ce que l'acheteur tape vraiment) et des SYNONYMES "
    "(ex. baskets/sneakers, canapé/sofa) pour capter plusieurs requêtes. Écris en LANGAGE "
    "NATUREL (les annonces Vinted sont indexées par Google). Description UNIQUE, sans bourrage "
    "de mots-clés. Termine par 3 à 5 hashtags ciblés.\n"
    "- hashtags : 3 à 5 maximum, pertinents (marque, taille, style, matière/état), jamais dans le titre.\n"
    "- attributs : propose les valeurs exactes à cocher dans les champs (marque, taille, couleur, "
    "matière, état, catégorie la plus précise) car remplir tous les champs = apparaître dans les filtres.\n"
    "Français correct, ton factuel et rassurant, aucune promesse mensongère."
)


def _prompt(produit: dict, mots_cles: list[str]) -> str:
    lignes = ["Crée une annonce de revente optimisée à partir de ces informations :", ""]
    libelles = {
        "nom": "Produit",
        "marque": "Marque",
        "categorie": "Catégorie",
        "etat": "État",
        "taille": "Taille",
        "couleur": "Couleur",
        "details": "Détails / défauts / accessoires",
        "plateforme": "Plateforme cible",
    }
    for cle, libelle in libelles.items():
        val = produit.get(cle)
        if val:
            lignes.append(f"- {libelle} : {val}")
    if mots_cles:
        lignes.append("")
        lignes.append("Mots-clés SEO à intégrer en priorité (les plus recherchés d'abord) : "
                      + ", ".join(mots_cles))
    lignes.append("")
    lignes.append(
        "Applique strictement les règles Vinted 2026 : titre 50-60 car. (marque + type + détail, "
        "mots-clés en tête, aucune majuscule criarde) ; description 80-150 mots en 3 blocs, longue "
        "traîne + synonymes, langage naturel ; 3-5 hashtags en fin de description ; attributs à remplir."
    )
    return "\n".join(lignes)


def generer_annonce(produit: dict, mots_cles: list[str] | None = None) -> dict:
    """Appelle Claude et renvoie le dict de l'annonce. Lève une exception en cas d'échec."""
    if anthropic is None:
        raise RuntimeError("Le paquet 'anthropic' n'est pas installé (pip install anthropic).")

    mots_cles = mots_cles or []
    client = anthropic.Anthropic()  # clé via ANTHROPIC_API_KEY ou profil ant

    resp = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        thinking={"type": "adaptive"},
        system=SYSTEME,
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": _prompt(produit, mots_cles)}],
    )

    texte = next((b.text for b in resp.content if b.type == "text"), None)
    if not texte:
        raise RuntimeError("Réponse Claude vide.")
    return json.loads(texte)
