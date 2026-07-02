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
    "Tu es un expert de la revente d'occasion sur Vinted et Leboncoin en France (règles 2026). "
    "Les deux plateformes classent d'abord sur le TEXTE (titre + description) et la FRAÎCHEUR, "
    "pas sur les hashtags. Tu écris en français correct, ton factuel et rassurant, sans promesse "
    "mensongère ni superlatif creux, en plaçant naturellement les mots-clés que les acheteurs tapent.\n\n"
    "Champs à produire :\n"
    "- titre_vinted : titre optimisé Vinted, 50-60 caractères, « marque + type + détail distinctif "
    "(taille/couleur/état) », mots-clés les plus recherchés EN TÊTE. Interdits : MAJUSCULES criardes, "
    "« URGENT », « PROMO !!! », ponctuation spammée, mots génériques seuls.\n"
    "- titre_court : titre Leboncoin, <= 50 caractères, DESCRIPTIF et précis (type + marque + détail "
    "+ quantité si utile), vocabulaire acheteur.\n"
    "- description : 80 à 150 mots, en 3 blocs — (1) accroche, (2) détails techniques (marque, "
    "dimensions/mesures, couleur, matière, état PRÉCIS et honnête), (3) conditions (envoi, négociation). "
    "Mots-clés LONGUE TRAÎNE + SYNONYMES (baskets/sneakers…), langage naturel (indexé par Google), "
    "description UNIQUE sans bourrage. Termine par les mots-clés/hashtags.\n"
    "- hashtags : mots-clés ciblés (marque, taille/type, style, matière/état), jamais dans le titre.\n"
    "- attributs : valeurs exactes à cocher (marque, taille, couleur, matière, état, catégorie la plus "
    "précise) car remplir tous les champs = apparaître dans les filtres.\n"
    "Respecte STRICTEMENT les règles spécifiques à la plateforme cible fournies dans le message."
)

# Règles propres à chaque plateforme, injectées selon la cible.
REGLES_VINTED = (
    "Règles VINTED : moteur de recherche par mots-clés ; les hashtags ne sont ni cliquables ni "
    "indexés (comptés comme du texte). Mets 3 à 5 hashtags MAX, uniquement en fin de description. "
    "Titre 50-60 car. riche en mots-clés. La fraîcheur prime : texte pensé pour la recherche interne "
    "ET Google."
)
REGLES_LEBONCOIN = (
    "Règles LEBONCOIN (impératif, sous peine de refus) : "
    "(1) le titre et la description doivent DÉCRIRE RÉELLEMENT le produit, pas de texte publicitaire "
    "ni généraliste ; titre descriptif et précis, sans « PROMO » ni majuscules criardes. "
    "(2) MAXIMUM 5 mots-clés liés à l'annonce, en fin de description (avec ou sans #) — donne donc "
    "AU PLUS 5 hashtags. "
    "(3) UNE annonce = UN seul bien : n'écris jamais « plusieurs disponibles », « autres modèles en "
    "stock » ni rien qui suggère un catalogue. "
    "(4) N'utilise PAS la marque d'un AUTRE produit pour décrire celui-ci. "
    "(5) État honnête : si l'emballage/la boîte est abîmé, écris « très bon état » ou « comme neuf », "
    "jamais « neuf sous blister ». "
    "(6) Catégorie la plus précise (ex. Maison & Jardin > Arts de la table)."
)


def _regles_plateforme(plateforme: str) -> str:
    p = (plateforme or "vinted").lower()
    if p == "leboncoin":
        return REGLES_LEBONCOIN
    if p in ("les-deux", "les deux", "both"):
        return REGLES_VINTED + "\n" + REGLES_LEBONCOIN + (
            "\nComme tu cibles LES DEUX : respecte la limite Leboncoin de 5 mots-clés max "
            "(donc au plus 5 hashtags, valable aussi pour Vinted)."
        )
    return REGLES_VINTED


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
    lignes.append(_regles_plateforme(produit.get("plateforme", "vinted")))
    return "\n".join(lignes)


# --------------------------------------------------------------------------- #
# Analyse de photo : identifier le produit pour pré-remplir le formulaire
# --------------------------------------------------------------------------- #
PRODUIT_SCHEMA = {
    "type": "object",
    "properties": {
        "nom": {"type": "string", "description": "Nom/modèle précis du produit (ex. « Coffret matcha 10 pièces », « iPhone 12 64 Go »)"},
        "marque": {"type": "string", "description": "Marque si visible/identifiable, sinon chaîne vide"},
        "categorie": {"type": "string", "description": "Catégorie de revente (Vêtement, High-tech, Jeux vidéo, Maison / déco…)"},
        "etat": {"type": "string", "description": "État apparent parmi : Neuf avec étiquette, Neuf sans étiquette, Très bon état, Bon état, Satisfaisant — ou vide si indéterminable"},
        "taille": {"type": "string", "description": "Taille/pointure/dimensions si visibles, sinon vide"},
        "couleur": {"type": "string", "description": "Couleur dominante"},
        "details": {"type": "string", "description": "Détails utiles pour l'annonce : contenu, accessoires visibles, défauts apparents (boîte abîmée…), édition"},
        "mots_cles": {"type": "array", "items": {"type": "string"}, "description": "5-10 mots-clés de recherche que les acheteurs taperaient"},
        "confiance": {"type": "string", "enum": ["haute", "moyenne", "basse"], "description": "Confiance dans l'identification"},
    },
    "required": ["nom", "marque", "categorie", "etat", "taille", "couleur", "details", "mots_cles", "confiance"],
    "additionalProperties": False,
}

SYSTEME_PHOTO = (
    "Tu identifies des produits d'occasion à partir d'une photo, pour pré-remplir une annonce "
    "de revente Vinted/Leboncoin. Lis les textes visibles (boîte, étiquette, logo) pour trouver "
    "marque et modèle exacts. Note honnêtement les défauts visibles (boîte marquée, rayures…). "
    "Si un champ n'est pas déterminable, renvoie une chaîne vide plutôt que d'inventer."
)


def analyser_photo(image_b64: str, media_type: str = "image/jpeg") -> dict:
    """Identifie le produit sur la photo et renvoie les champs du formulaire."""
    if anthropic is None:
        raise RuntimeError("Le paquet 'anthropic' n'est pas installé (pip install anthropic).")

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=1200,
        thinking={"type": "adaptive"},
        system=SYSTEME_PHOTO,
        output_config={"format": {"type": "json_schema", "schema": PRODUIT_SCHEMA}},
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                {"type": "text", "text": "Identifie ce produit et remplis les champs pour l'annonce de revente."},
            ],
        }],
    )
    texte = next((b.text for b in resp.content if b.type == "text"), None)
    if not texte:
        raise RuntimeError("Réponse Claude vide.")
    return json.loads(texte)


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
