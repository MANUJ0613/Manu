#!/usr/bin/env python3
"""Serveur Flask 24/7 : générateur d'annonces SEO + suivi de republication.

Pensé pour tourner en continu sur un VPS (systemd) et t'aider à revendre plus
vite sur Leboncoin et Vinted :

  • Génère titre + description optimisés (API Claude)
  • Récupère les vrais volumes de mots-clés Google (DataForSEO)
  • Calcule prix conseillé + marge nette (frais Vinted/Leboncoin)
  • Boutons Google Lens / eBay vendus / Gemini pour caler le prix
  • Suit tes annonces avec un statut 🟢🟠🔴 de fraîcheur
  • Envoie des alertes ntfy aux meilleurs créneaux pour republier
  • Détecte TES meilleurs créneaux à partir de tes ventes

Lancement local :   python annonces_seo.py
Prod (systemd)  :   gunicorn -w 2 -b 0.0.0.0:8000 annonces_seo:app
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime

from flask import Flask, jsonify, render_template, request

from seo_tools import db, dataforseo, links, notify, pricing, slots

try:
    from seo_tools import claude_client
except Exception:  # anthropic éventuellement absent : l'app démarre quand même
    claude_client = None  # type: ignore

app = Flask(__name__)

PUBLIC_URL = os.environ.get("ANNONCES_PUBLIC_URL", "")
# Intervalle du planificateur d'alertes (secondes)
SCHED_INTERVAL = int(os.environ.get("ANNONCES_SCHED_INTERVAL", "300"))
# Envoyer aussi les 🟠 (True) ou seulement les 🔴 (False)
ALERTE_INCLURE_ORANGE = os.environ.get("ALERTE_INCLURE_ORANGE", "true").lower() == "true"


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #
@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/etat")
def etat():
    """État de configuration : ce qui est branché ou non."""
    return jsonify({
        "claude": claude_client is not None and bool(
            os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        ),
        "dataforseo": dataforseo.disponible(),
        "ntfy": notify.disponible(),
        "ntfy_topic": notify.TOPIC if notify.disponible() else None,
        "modele": getattr(claude_client, "MODEL", None) if claude_client else None,
    })


# --------------------------------------------------------------------------- #
# Génération d'annonce
# --------------------------------------------------------------------------- #
@app.post("/api/generer")
def generer():
    data = request.get_json(force=True, silent=True) or {}
    produit = {
        "nom": (data.get("nom") or "").strip(),
        "marque": (data.get("marque") or "").strip(),
        "categorie": (data.get("categorie") or "").strip(),
        "etat": (data.get("etat") or "").strip(),
        "taille": (data.get("taille") or "").strip(),
        "couleur": (data.get("couleur") or "").strip(),
        "details": (data.get("details") or "").strip(),
        "plateforme": (data.get("plateforme") or "vinted").strip(),
    }
    if not produit["nom"]:
        return jsonify({"erreur": "Le nom du produit est requis."}), 400

    # 1) Mots-clés candidats -> volumes réels DataForSEO
    candidats = _mots_cles_candidats(produit, data.get("mots_cles"))
    seo = dataforseo.volumes_mots_cles(candidats)
    tops = (
        [m["keyword"] for m in seo["mots_cles"][:8]]
        if seo["disponible"] else candidats[:8]
    )

    # 2) Génération de l'annonce
    annonce = None
    erreur_claude = None
    if claude_client is not None:
        try:
            annonce = claude_client.generer_annonce(produit, tops)
        except Exception as e:  # clé absente, réseau, quota...
            erreur_claude = str(e)
    else:
        erreur_claude = "Module Claude indisponible (installe 'anthropic')."

    # 3) Prix + marge
    chiffrage = None
    prix_achat = _to_float(data.get("prix_achat"))
    ref_marche = _to_float(data.get("reference_marche"))
    if prix_achat is not None:
        chiffrage = pricing.suggestions_prix(prix_achat, produit["plateforme"], ref_marche)

    # 4) Liens externes (Lens / eBay / Gemini)
    liens = links.liens_produit(produit, image_url=data.get("image_url"))

    return jsonify({
        "produit": produit,
        "annonce": annonce,
        "erreur_claude": erreur_claude,
        "seo": seo,
        "mots_cles_utilises": tops,
        "chiffrage": chiffrage,
        "liens": liens,
    })


@app.post("/api/prix")
def prix():
    """Recalcul de prix/marge à la volée (curseur de marge côté UI)."""
    data = request.get_json(force=True, silent=True) or {}
    prix_achat = _to_float(data.get("prix_achat")) or 0
    plateforme = data.get("plateforme", "vinted")
    if data.get("prix_vente") is not None:
        c = pricing.chiffrer(prix_achat, _to_float(data.get("prix_vente")) or 0, plateforme)
        return jsonify(c.to_dict())
    marge = _to_float(data.get("marge_cible_pct"))
    if marge is not None:
        pv = pricing.prix_pour_marge(prix_achat, marge, plateforme)
        return jsonify(pricing.chiffrer(prix_achat, pv, plateforme).to_dict())
    return jsonify(pricing.suggestions_prix(prix_achat, plateforme))


# --------------------------------------------------------------------------- #
# Suivi des annonces
# --------------------------------------------------------------------------- #
@app.get("/api/annonces")
def api_annonces():
    statut = request.args.get("statut", "active") or None
    items = slots.annoter(db.lister_annonces(statut))
    resume = {
        "vert": sum(1 for a in items if a["statut_couleur"] == "vert"),
        "orange": sum(1 for a in items if a["statut_couleur"] == "orange"),
        "rouge": sum(1 for a in items if a["statut_couleur"] == "rouge"),
    }
    return jsonify({"annonces": items, "resume": resume})


@app.post("/api/annonces")
def api_creer_annonce():
    data = request.get_json(force=True, silent=True) or {}
    if not (data.get("titre") or "").strip():
        return jsonify({"erreur": "Titre requis."}), 400
    aid = db.creer_annonce(
        titre=data["titre"].strip(),
        plateforme=data.get("plateforme", "vinted"),
        categorie=data.get("categorie"),
        prix=_to_float(data.get("prix")),
        prix_achat=_to_float(data.get("prix_achat")),
        url=data.get("url"),
        note=data.get("note"),
    )
    return jsonify(slots.statut_annonce(db.get_annonce(aid))), 201


@app.post("/api/annonces/<int:aid>/republier")
def api_republier(aid: int):
    a = db.get_annonce(aid)
    if not a:
        return jsonify({"erreur": "introuvable"}), 404
    data = request.get_json(force=True, silent=True) or {}
    # Garde-fou anti-spam : Vinted pénalise les reposts trop rapprochés.
    # Ne s'applique qu'après une vraie republication (pas au 1er bump après création).
    if not data.get("force") and a["nb_republications"] >= 1 and slots.republication_trop_recente(a):
        return jsonify({
            "avertissement": (
                "Déjà republiée il y a moins de 24 h. Republier trop souvent la même "
                "fiche fait chuter la visibilité (détection de doublons Vinted). "
                "Confirme si tu veux quand même republier."
            ),
            "annonce": slots.statut_annonce(a),
        }), 409
    db.republier(aid)
    return jsonify(slots.statut_annonce(db.get_annonce(aid)))


@app.post("/api/annonces/<int:aid>/vendu")
def api_vendu(aid: int):
    if not db.get_annonce(aid):
        return jsonify({"erreur": "introuvable"}), 404
    data = request.get_json(force=True, silent=True) or {}
    db.marquer_vendu(aid, _to_float(data.get("montant")))
    return jsonify({"ok": True})


@app.delete("/api/annonces/<int:aid>")
def api_supprimer(aid: int):
    db.supprimer_annonce(aid)
    return jsonify({"ok": True})


# --------------------------------------------------------------------------- #
# Créneaux + ventes
# --------------------------------------------------------------------------- #
@app.get("/api/creneaux")
def api_creneaux():
    return jsonify(slots.meilleurs_creneaux(db.lister_ventes()))


@app.post("/api/ventes")
def api_ajouter_vente():
    """Ajout manuel d'une vente (pour alimenter la détection de créneaux)."""
    data = request.get_json(force=True, silent=True) or {}
    ts = data.get("date_vente")
    if ts is None:
        ts = time.time()
    else:
        ts = float(ts)
    db.ajouter_vente(
        date_vente=ts,
        montant=_to_float(data.get("montant")),
        plateforme=data.get("plateforme", "vinted"),
        annonce_id=data.get("annonce_id"),
    )
    return jsonify({"ok": True}), 201


@app.post("/api/tester-ntfy")
def api_tester_ntfy():
    ok = notify.envoyer("Test depuis ton outil de revente ✅", titre="Test ntfy", tags=["white_check_mark"])
    return jsonify({"envoye": ok, "configure": notify.disponible()})


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _to_float(v) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _mots_cles_candidats(produit: dict, saisis: str | None) -> list[str]:
    """Construit une liste de mots-clés candidats à partir du produit + saisie libre."""
    base: list[str] = []
    if saisis:
        base += [m.strip() for m in saisis.replace("\n", ",").split(",") if m.strip()]
    marque = produit.get("marque", "")
    nom = produit.get("nom", "")
    if nom:
        base.append(nom)
    if marque and nom:
        base.append(f"{marque} {nom}")
    if marque:
        base.append(marque)
    for extra in ("categorie", "taille", "couleur"):
        val = produit.get(extra)
        if val and nom:
            base.append(f"{nom} {val}")
    # dédoublonne en gardant l'ordre
    return list(dict.fromkeys([m for m in base if m]))


# --------------------------------------------------------------------------- #
# Planificateur d'alertes (thread de fond)
# --------------------------------------------------------------------------- #
def _boucle_alertes():
    """Vérifie régulièrement s'il faut alerter (bon créneau + annonces à republier)."""
    while True:
        try:
            _tick_alerte()
        except Exception as e:  # ne jamais tuer le thread
            app.logger.warning("alerte: %s", e)
        time.sleep(SCHED_INTERVAL)


def _tick_alerte():
    if not notify.disponible():
        return
    maintenant = datetime.now()
    creneaux = slots.meilleurs_creneaux(db.lister_ventes())["creneaux"]
    if not slots.est_bon_creneau(creneaux, maintenant):
        return

    # Anti-spam : une seule alerte par tranche horaire de bon créneau.
    cle_creneau = maintenant.strftime("%Y-%m-%d-%H")
    if db.get_reglage("derniere_alerte_creneau") == cle_creneau:
        return

    cibles = slots.a_republier(db.lister_annonces("active"), ALERTE_INCLURE_ORANGE)
    if not cibles:
        return

    if notify.alerte_republication(cibles, PUBLIC_URL):
        db.set_reglage("derniere_alerte_creneau", cle_creneau)


def _demarrer_scheduler():
    t = threading.Thread(target=_boucle_alertes, daemon=True, name="alertes-republication")
    t.start()


# Init base + scheduler dès l'import (fonctionne aussi sous gunicorn).
db.init_db()
if os.environ.get("ANNONCES_SCHEDULER", "true").lower() == "true":
    _demarrer_scheduler()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
