"""Liens externes d'aide à la revente : Google Lens, eBay (vendus), Gemini.

- Google Lens : recherche visuelle inverse pour identifier un produit / voir des
  annonces similaires. Sans URL d'image on ouvre Lens ; avec une URL d'image on
  lance directement la recherche par image.
- eBay "vendus" : la meilleure référence de PRIX réel — on filtre sur les objets
  effectivement vendus et clôturés.
- Gemini : on prépare un prompt d'estimation de prix à coller (Gemini n'accepte
  pas de prompt pré-rempli par URL de façon fiable).
"""
from __future__ import annotations

import urllib.parse


def lien_lens(image_url: str | None = None) -> str:
    if image_url:
        return "https://lens.google.com/uploadbyurl?url=" + urllib.parse.quote(image_url, safe="")
    return "https://lens.google.com/"


def lien_ebay_vendus(requete: str) -> str:
    """Page eBay France des objets VENDUS correspondant à la requête (référence prix)."""
    q = urllib.parse.quote(requete)
    return f"https://www.ebay.fr/sch/i.html?_nkw={q}&LH_Sold=1&LH_Complete=1&_sop=13"


def lien_gemini() -> str:
    return "https://gemini.google.com/app"


def prompt_gemini(produit: dict, reference_ebay: str = "") -> str:
    """Prompt à copier dans Gemini pour estimer un prix de revente."""
    desc = ", ".join(
        f"{k}: {v}" for k, v in produit.items()
        if v and k in ("nom", "marque", "categorie", "etat", "taille", "couleur", "details")
    )
    txt = (
        "Estime le prix de revente d'occasion en France (Vinted et Leboncoin) pour cet article. "
        "Donne une fourchette basse/haute et un prix conseillé, en te basant sur le marché actuel.\n\n"
        f"Article : {desc}"
    )
    if reference_ebay:
        txt += f"\n\nRéférence eBay (objets vendus) : {reference_ebay}"
    return txt


def liens_produit(produit: dict, image_url: str | None = None) -> dict:
    """Construit tous les liens/aides pour un produit donné."""
    requete = " ".join(
        str(produit.get(k)) for k in ("marque", "nom", "taille") if produit.get(k)
    ).strip() or str(produit.get("nom", ""))
    ebay = lien_ebay_vendus(requete)
    return {
        "lens": lien_lens(image_url),
        "ebay_vendus": ebay,
        "gemini": lien_gemini(),
        "gemini_prompt": prompt_gemini(produit, ebay),
        "requete": requete,
    }
