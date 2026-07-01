"""Génération de l'annonce (titre + description) via l'API Claude.

On demande à Claude une sortie JSON structurée, adaptée à la revente sur
Leboncoin et Vinted :
- titre_court     : ≤ 50 caractères (limite du titre Leboncoin)
- titre_vinted    : titre accrocheur optimisé pour la recherche Vinted
- description     : description prête à coller, avec les mots-clés placés
                    naturellement
- hashtags        : mots-dièse pour Vinted (sans le #, on l'ajoute côté UI)
- mots_cles_places: mots-clés SEO réellement intégrés

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
        "titre_vinted": {"type": "string", "description": "Titre accrocheur optimisé recherche Vinted"},
        "description": {"type": "string", "description": "Description prête à coller"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "mots_cles_places": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["titre_court", "titre_vinted", "description", "hashtags", "mots_cles_places"],
    "additionalProperties": False,
}

SYSTEME = (
    "Tu es un expert de la revente d'occasion sur Leboncoin et Vinted en France. "
    "Tu rédiges des annonces qui se vendent vite : titres accrocheurs et riches en "
    "mots-clés que les acheteurs tapent réellement, descriptions honnêtes, concrètes "
    "et rassurantes. Tu écris en français, sans superlatifs creux ni fautes. "
    "Tu places naturellement les mots-clés SEO fournis, sans bourrage. "
    "Le titre court fait 50 caractères maximum (contrainte Leboncoin). "
    "Tu adaptes le ton à la plateforme cible."
)


def _prompt(produit: dict, mots_cles: list[str]) -> str:
    lignes = ["Crée une annonce de revente à partir de ces informations :", ""]
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
        "Consignes : titre_court <= 50 caractères ; titre_vinted plus riche en mots-clés ; "
        "description structurée (état réel, caractéristiques, pourquoi acheter, modalités) ; "
        "5 à 10 hashtags Vinted pertinents ; reste factuel."
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
