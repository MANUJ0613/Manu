"""Stockage SQLite : annonces suivies, ventes (pour les créneaux) et réglages.

On garde tout dans un seul fichier SQLite (state/annonces.db par défaut) pour
rester déployable sans base externe. L'accès est protégé par un verrou : Flask
sert plusieurs requêtes et le planificateur tourne dans un thread à part.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterable

# Emplacement de la base : configurable, sinon state/annonces.db à côté du repo.
DB_PATH = os.environ.get(
    "ANNONCES_DB",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state", "annonces.db"),
)

_LOCK = threading.RLock()


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_conn():
    """Connexion partagée + verrou (SQLite n'aime pas l'écriture concurrente)."""
    with _LOCK:
        conn = _connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS annonces (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    titre             TEXT NOT NULL,
    plateforme        TEXT NOT NULL DEFAULT 'vinted',   -- vinted | leboncoin
    categorie         TEXT,
    prix              REAL,
    prix_achat        REAL,
    url               TEXT,
    statut            TEXT NOT NULL DEFAULT 'active',    -- active | vendu | archive
    date_publication  REAL NOT NULL,                     -- timestamp epoch (s)
    date_republication REAL NOT NULL,                    -- dernière (re)publication
    nb_republications INTEGER NOT NULL DEFAULT 0,
    note              TEXT,
    cree_le           REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS ventes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    annonce_id INTEGER,
    plateforme TEXT NOT NULL DEFAULT 'vinted',
    montant    REAL,
    date_vente REAL NOT NULL,                            -- timestamp epoch (s)
    FOREIGN KEY (annonce_id) REFERENCES annonces(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS reglages (
    cle    TEXT PRIMARY KEY,
    valeur TEXT
);

CREATE INDEX IF NOT EXISTS idx_annonces_statut ON annonces(statut);
CREATE INDEX IF NOT EXISTS idx_ventes_date ON ventes(date_vente);
"""


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)


# --------------------------------------------------------------------------- #
# Annonces
# --------------------------------------------------------------------------- #
def creer_annonce(**kw: Any) -> int:
    now = time.time()
    champs = {
        "titre": kw["titre"],
        "plateforme": kw.get("plateforme", "vinted"),
        "categorie": kw.get("categorie"),
        "prix": kw.get("prix"),
        "prix_achat": kw.get("prix_achat"),
        "url": kw.get("url"),
        "statut": kw.get("statut", "active"),
        "date_publication": kw.get("date_publication", now),
        "date_republication": kw.get("date_republication", now),
        "nb_republications": kw.get("nb_republications", 0),
        "note": kw.get("note"),
        "cree_le": now,
    }
    cols = ", ".join(champs.keys())
    ph = ", ".join(["?"] * len(champs))
    with get_conn() as conn:
        cur = conn.execute(f"INSERT INTO annonces ({cols}) VALUES ({ph})", tuple(champs.values()))
        return cur.lastrowid


def lister_annonces(statut: str | None = "active") -> list[dict]:
    q = "SELECT * FROM annonces"
    params: tuple = ()
    if statut:
        q += " WHERE statut = ?"
        params = (statut,)
    q += " ORDER BY date_republication ASC"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(q, params).fetchall()]


def get_annonce(annonce_id: int) -> dict | None:
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM annonces WHERE id = ?", (annonce_id,)).fetchone()
        return dict(r) if r else None


def republier(annonce_id: int) -> None:
    """Marque une annonce comme republiée maintenant (remet le compteur à zéro)."""
    now = time.time()
    with get_conn() as conn:
        conn.execute(
            "UPDATE annonces SET date_republication = ?, nb_republications = nb_republications + 1 "
            "WHERE id = ?",
            (now, annonce_id),
        )


def maj_annonce(annonce_id: int, **kw: Any) -> None:
    if not kw:
        return
    cols = ", ".join(f"{k} = ?" for k in kw)
    with get_conn() as conn:
        conn.execute(f"UPDATE annonces SET {cols} WHERE id = ?", (*kw.values(), annonce_id))


def marquer_vendu(annonce_id: int, montant: float | None = None) -> None:
    """Passe l'annonce en 'vendu' et enregistre la vente (sert aux créneaux)."""
    now = time.time()
    a = get_annonce(annonce_id)
    plateforme = a["plateforme"] if a else "vinted"
    if montant is None and a:
        montant = a.get("prix")
    with get_conn() as conn:
        conn.execute("UPDATE annonces SET statut = 'vendu' WHERE id = ?", (annonce_id,))
        conn.execute(
            "INSERT INTO ventes (annonce_id, plateforme, montant, date_vente) VALUES (?, ?, ?, ?)",
            (annonce_id, plateforme, montant, now),
        )


def supprimer_annonce(annonce_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM annonces WHERE id = ?", (annonce_id,))


# --------------------------------------------------------------------------- #
# Ventes (import de stats + détection de créneaux)
# --------------------------------------------------------------------------- #
def ajouter_vente(date_vente: float, montant: float | None = None,
                  plateforme: str = "vinted", annonce_id: int | None = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO ventes (annonce_id, plateforme, montant, date_vente) VALUES (?, ?, ?, ?)",
            (annonce_id, plateforme, montant, date_vente),
        )
        return cur.lastrowid


def lister_ventes(depuis: float | None = None) -> list[dict]:
    q = "SELECT * FROM ventes"
    params: tuple = ()
    if depuis:
        q += " WHERE date_vente >= ?"
        params = (depuis,)
    q += " ORDER BY date_vente ASC"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(q, params).fetchall()]


# --------------------------------------------------------------------------- #
# Réglages clé/valeur
# --------------------------------------------------------------------------- #
def get_reglage(cle: str, defaut: str | None = None) -> str | None:
    with get_conn() as conn:
        r = conn.execute("SELECT valeur FROM reglages WHERE cle = ?", (cle,)).fetchone()
        return r["valeur"] if r else defaut


def set_reglage(cle: str, valeur: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO reglages (cle, valeur) VALUES (?, ?) "
            "ON CONFLICT(cle) DO UPDATE SET valeur = excluded.valeur",
            (cle, valeur),
        )
