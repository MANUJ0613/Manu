"""Client DataForSEO : vrais volumes de recherche Google pour tes mots-clés.

On utilise l'endpoint "Google Ads Search Volume" (live) qui renvoie, par
mot-clé : volume mensuel, concurrence et CPC — les mêmes données que Google
Keyword Planner.

    POST https://api.dataforseo.com/v3/keywords_data/google_ads/search_volume/live

Authentification : Basic (login = e-mail du compte, mot de passe = clé API).
Configurez DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD dans le .env.

Sans identifiants (ou en cas d'erreur réseau), on renvoie un résultat vide et
l'appelant continue : la génération d'annonce marche quand même, juste sans les
volumes réels.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.request
import urllib.error

API_URL = "https://api.dataforseo.com/v3/keywords_data/google_ads/search_volume/live"

LOGIN = os.environ.get("DATAFORSEO_LOGIN", "")
PASSWORD = os.environ.get("DATAFORSEO_PASSWORD", "")
LOCATION = os.environ.get("DATAFORSEO_LOCATION", "France")
LANGUAGE = os.environ.get("DATAFORSEO_LANGUAGE", "French")


def disponible() -> bool:
    return bool(LOGIN and PASSWORD)


def volumes_mots_cles(mots_cles: list[str], timeout: int = 25) -> dict:
    """Renvoie {'disponible': bool, 'mots_cles': [{keyword, volume, competition, cpc}], 'erreur': str|None}.

    Les mots-clés sont triés par volume décroissant.
    """
    mots_cles = [m.strip() for m in mots_cles if m and m.strip()]
    mots_cles = list(dict.fromkeys(mots_cles))[:100]  # dédoublonne, max 100 (limite API)
    if not mots_cles:
        return {"disponible": False, "mots_cles": [], "erreur": "aucun mot-clé"}
    if not disponible():
        return {"disponible": False, "mots_cles": [], "erreur": "DataForSEO non configuré"}

    payload = json.dumps([{
        "keywords": mots_cles,
        "location_name": LOCATION,
        "language_name": LANGUAGE,
    }]).encode("utf-8")

    token = base64.b64encode(f"{LOGIN}:{PASSWORD}".encode()).decode()
    req = urllib.request.Request(
        API_URL,
        data=payload,
        headers={
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"disponible": False, "mots_cles": [], "erreur": f"HTTP {e.code}"}
    except Exception as e:  # réseau, timeout, JSON...
        return {"disponible": False, "mots_cles": [], "erreur": str(e)}

    resultats = []
    try:
        for task in data.get("tasks", []):
            for res in (task.get("result") or []):
                kw = res.get("keyword")
                if not kw:
                    continue
                resultats.append({
                    "keyword": kw,
                    "volume": res.get("search_volume") or 0,
                    "competition": res.get("competition"),           # LOW/MEDIUM/HIGH ou None
                    "competition_index": res.get("competition_index"),
                    "cpc": res.get("cpc"),
                })
    except (AttributeError, TypeError) as e:
        return {"disponible": False, "mots_cles": [], "erreur": f"réponse inattendue: {e}"}

    resultats.sort(key=lambda r: (r["volume"] or 0), reverse=True)
    return {"disponible": True, "mots_cles": resultats, "erreur": None}


def meilleurs_mots_cles(mots_cles: list[str], top: int = 8) -> list[str]:
    """Renvoie juste la liste des mots-clés les plus recherchés (pour nourrir Claude)."""
    r = volumes_mots_cles(mots_cles)
    if not r["disponible"]:
        return mots_cles[:top]
    return [m["keyword"] for m in r["mots_cles"][:top]]


# --------------------------------------------------------------------------- #
# Tri en 3 paquets selon le volume mensuel France
# --------------------------------------------------------------------------- #
# >= SEUIL_FORT  -> réservés au TITRE (empiler un max dans 50-60 caractères)
# >= SEUIL_MOYEN -> à intégrer naturellement dans la DESCRIPTION
# en dessous     -> faibles (complément, souvent ignorés)
SEUIL_FORT = float(os.environ.get("SEO_SEUIL_FORT", "1000"))
SEUIL_MOYEN = float(os.environ.get("SEO_SEUIL_MOYEN", "100"))


def trier_par_volume(resultats: list[dict]) -> dict:
    """Trie les résultats de volumes_mots_cles() en paquets fort/moyen/faible.

    Si aucun mot n'atteint le seuil fort (produit de niche), on promeut les
    3 meilleurs 'moyen' en 'fort' pour ne jamais laisser le titre sans mots-clés.
    """
    fort, moyen, faible = [], [], []
    for m in resultats:  # déjà triés par volume décroissant
        v = m.get("volume") or 0
        if v >= SEUIL_FORT:
            fort.append(m["keyword"])
        elif v >= SEUIL_MOYEN:
            moyen.append(m["keyword"])
        else:
            faible.append(m["keyword"])
    if not fort and moyen:
        fort, moyen = moyen[:3], moyen[3:]
    return {"fort": fort, "moyen": moyen, "faible": faible}
