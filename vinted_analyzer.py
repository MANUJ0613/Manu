#!/usr/bin/env python3
"""
Analyseur de demande Vinted — savoir CE QUI EST LE PLUS RECHERCHÉ.

Objectif : pour une liste de recherches / marques (la "watchlist"), interroger
le catalogue Vinted et classer les articles par DEMANDE, c'est-à-dire par
nombre de FAVORIS (likes) et de VUES. On voit ainsi, sans deviner, ce qui
s'arrache et à quel prix ça se revend — pour décider quoi sourcer.

Principe :
  1. Pour chaque recherche, on lit le catalogue Vinted via son API JSON
     (/api/v2/catalog/items) — chaque article renvoie son nombre de FAVORIS.
  2. (Option) On enrichit les meilleurs articles avec leur nombre de VUES,
     lu sur la fiche article (/api/v2/items/<id>) — la liste catalogue ne
     contient pas les vues.
  3. On agrège par recherche (favoris/vues moyens, prix médian de revente…)
     et on calcule un SCORE DE DEMANDE = favoris + poids·vues.
  4. On sort un classement : les recherches les plus convoitées + le top des
     articles les plus likés/vus, dans la console, en JSON, en CSV et
     (option) en résumé Discord / Telegram.

Vinted est protégé par DataDome (comme Micromania) : sur une IP datacenter,
les requêtes nues sont bloquées. On réutilise donc l'impersonation TLS de
Chrome via curl_cffi + un warm-up de la home pour récupérer les cookies de
session. Sans curl_cffi, repli urllib (OK sur une IP non bloquée).

Configuration via variables d'environnement (voir README.md).
"""

from __future__ import annotations

import csv
import gzip
import json
import os
import re
import socket
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

# --------------------------------------------------------------------------- #
# Configuration (surchargée par l'environnement)
# --------------------------------------------------------------------------- #

# Domaine Vinted ciblé (vinted.fr, vinted.de, vinted.com…).
VINTED_DOMAIN = os.environ.get("VINTED_DOMAIN", "www.vinted.fr").strip()
SITE_ROOT = f"https://{VINTED_DOMAIN}"
API_ROOT = f"{SITE_ROOT}/api/v2"

# La watchlist : ce qu'on surveille. Soit en variable d'env (séparé par des
# virgules ou des sauts de ligne), soit dans un fichier (une recherche par
# ligne, '#' = commentaire). Une "recherche" = un mot-clé, un produit, une
# marque… exactement ce que tu taperais dans la barre de recherche Vinted.
VINTED_QUERIES = os.environ.get("VINTED_QUERIES", "").strip()
WATCHLIST_FILE = os.environ.get("WATCHLIST_FILE", "watchlist.txt")

# Combien de pages de catalogue lire par recherche (96 articles/page).
PER_PAGE = int(os.environ.get("PER_PAGE", "96"))
MAX_PAGES = int(os.environ.get("MAX_PAGES", "3"))  # ~288 articles / recherche

# Filtres de catalogue (facultatifs), appliqués à toutes les recherches.
PRICE_FROM = os.environ.get("PRICE_FROM", "").strip()
PRICE_TO = os.environ.get("PRICE_TO", "").strip()
# Ordre Vinted : relevance / newest_first / price_low_to_high / price_high_to_low.
# "relevance" met en avant ce que Vinted juge le plus pertinent/populaire.
CATALOG_ORDER = os.environ.get("CATALOG_ORDER", "relevance").strip()
CURRENCY = os.environ.get("CURRENCY", "EUR").strip()

# VUES : Vinted ne les expose plus dans le catalogue (toujours 0) ni en
# anonyme sur la fiche article. Elles ne sont renvoyées qu'à un compte
# CONNECTÉ. Pour les obtenir, fournis ta session via VINTED_COOKIE (le
# header Cookie copié depuis ton navigateur) ou VINTED_ACCESS_TOKEN, puis
# FETCH_VIEWS=true : on lira alors la fiche des TOP_VIEWS meilleurs articles
# (par favoris) de chaque recherche. Sans session, on s'appuie sur les FAVORIS
# (toujours disponibles), qui suffisent à mesurer la demande.
FETCH_VIEWS = os.environ.get("FETCH_VIEWS", "false").lower() == "true"
TOP_VIEWS = int(os.environ.get("TOP_VIEWS", "20"))  # articles enrichis / recherche

# Session connectée (facultative) pour débloquer les VUES.
VINTED_COOKIE = os.environ.get("VINTED_COOKIE", "").strip()
VINTED_ACCESS_TOKEN = os.environ.get("VINTED_ACCESS_TOKEN", "").strip()

# Score de demande = favoris + VIEW_WEIGHT * vues. Les vues sont bien plus
# nombreuses que les favoris ; on les pondère donc plus faiblement.
VIEW_WEIGHT = float(os.environ.get("VIEW_WEIGHT", "0.05"))

# Combien d'articles afficher dans le top global du rapport.
TOP_ITEMS = int(os.environ.get("TOP_ITEMS", "30"))

# --------------------------------------------------------------------------- #
# Mode SCAN CATÉGORIES — trouver les produits récents les plus likés, toutes
# catégories SAUF les vêtements. C'est le mode par défaut.
#   MODE=categories : scanne l'arbre des catégories Vinted (hors vêtements),
#       filtre aux articles postés depuis DAYS_WINDOW jours, classe par favoris.
#   MODE=watchlist  : ancien mode, analyse les recherches de la watchlist.
# --------------------------------------------------------------------------- #
MODE = os.environ.get("MODE", "categories").strip().lower()
# Fenêtre de fraîcheur : on ne garde que les articles postés depuis N jours.
DAYS_WINDOW = float(os.environ.get("DAYS_WINDOW", "7"))
# Critère de classement du top :
#   "hotness"    -> favoris PAR JOUR : privilégie ce qui monte vite (le + frais) ;
#   "favourites" -> favoris totaux : privilégie les annonces les plus likées.
RANK_BY = os.environ.get("RANK_BY", "hotness").strip().lower()
# Pages (de 96) lues par catégorie en mode scan (relevance remonte les
# articles récents les plus engageants en premier).
CATEGORY_MAX_PAGES = int(os.environ.get("CATEGORY_MAX_PAGES", "3"))
# On ignore le bruit : n'afficher que les articles ayant au moins X favoris.
MIN_FAVOURITES = int(os.environ.get("MIN_FAVOURITES", "3"))
# Catégories à EXCLURE par titre (insensible casse/accents). Par défaut on
# retire les vêtements et la mode créateurs ; on GARDE chaussures, sacs,
# accessoires, électronique, maison, collections, jouets, sport…
EXCLUDE_PATTERNS = [
    p.strip().lower()
    for p in os.environ.get(
        "EXCLUDE_PATTERNS", "vêtement,vetement,créateur,createur"
    ).split(",")
    if p.strip()
]
# Forcer une liste d'IDs de catégories (sinon arbre auto). Ex: "2994,4824,1918"
VINTED_CATEGORIES = [
    c.strip()
    for c in os.environ.get("VINTED_CATEGORIES", "").split(",")
    if c.strip()
]
# Combien d'articles afficher PAR catégorie (digest groupé par catégorie).
TOP_PER_CATEGORY = int(os.environ.get("TOP_PER_CATEGORY", "15"))

# --- Suivi des tendances dans le temps ---
# À chaque run on mémorise un instantané (favoris cumulés par mot-clé /
# sous-catégorie / annonce) et on le compare au run précédent pour repérer CE
# QUI MONTE — la vraie détection de tendance, avant que ça explose.
TRACK_TRENDS = os.environ.get("TRACK_TRENDS", "true").lower() == "true"
HISTORY_FILE = os.environ.get("HISTORY_FILE", "state/vinted_history.json")
HISTORY_MAX_RUNS = int(os.environ.get("HISTORY_MAX_RUNS", "60"))
TOP_TRENDS = int(os.environ.get("TOP_TRENDS", "12"))  # tendances montantes affichées

# --- Mode "brands" : classement des MARQUES d'une/des catégorie(s) ---
# Plusieurs tris pour dépasser le plafond ~960 résultats/requête.
BRAND_ORDERS = [
    o.strip()
    for o in os.environ.get(
        "BRAND_ORDERS",
        "relevance,newest_first,price_high_to_low,price_low_to_high",
    ).split(",")
    if o.strip()
]
BRAND_MIN_LISTINGS = int(os.environ.get("BRAND_MIN_LISTINGS", "3"))
# Filtre de fraîcheur du mode marques :
#   0  -> toute l'offre active (favoris cumulés depuis la mise en ligne) ;
#   N  -> ne compter que les annonces postées depuis N jours (tendance récente).
BRAND_DAYS_WINDOW = float(os.environ.get("BRAND_DAYS_WINDOW", "0"))
TOP_BRANDS = int(os.environ.get("TOP_BRANDS", "40"))
BRANDS_JSON = os.environ.get("BRANDS_JSON", "state/vinted_brands.json")
BRANDS_CSV = os.environ.get("BRANDS_CSV", "state/vinted_brands.csv")
# "Marques" qui n'en sont pas (bruit de saisie) — exclues du classement.
BRAND_NOISE = {
    s.strip().lower()
    for s in (
        "inconnu,diverse,divers,amazon,jeu,sans marque,pas de marque,"
        "je ne sais pas,aucun,ohne,unbekannt,collection,collezione,accessories,"
        "accessoires,baby,piano,excellent,peluche,great toys,reborn,fait main,"
        "hecho a mano,handarbeit,diamond painting,kpop,autre,other,na,no,"
        "sansnom.,various,rare,neuf,marque,vinted"
    ).split(",")
}

# --- Mode "deals" : scanner d'affaires (annonces sous le prix du marché) ---
# Seuil de réduction vs prix médian du marché pour déclencher une alerte.
DEAL_THRESHOLD = float(os.environ.get("DEAL_THRESHOLD", "0.40"))  # -40%
# Un candidat doit être posté depuis <= N jours (deal frais à sniper).
DEAL_MAX_AGE_DAYS = float(os.environ.get("DEAL_MAX_AGE_DAYS", "2"))
# Pages pour bâtir la distribution de prix (relevance) et trouver les candidats (newest).
DEAL_REF_PAGES = int(os.environ.get("DEAL_REF_PAGES", "4"))
DEAL_NEW_PAGES = int(os.environ.get("DEAL_NEW_PAGES", "2"))
# Nombre minimum d'annonces comparables pour estimer un prix de marché fiable.
DEAL_MIN_COMPARABLES = int(os.environ.get("DEAL_MIN_COMPARABLES", "5"))
DEAL_MIN_PRICE = float(os.environ.get("DEAL_MIN_PRICE", "5"))  # ignore les micro-prix
# Mots de modèle communs requis pour considérer 2 annonces comparables (>=2 évite
# de comparer une "manette switch" à une "console switch").
DEAL_MIN_SHARED_TOKENS = int(os.environ.get("DEAL_MIN_SHARED_TOKENS", "2"))
DEAL_STATE_FILE = os.environ.get("DEAL_STATE_FILE", "state/vinted_deals_state.json")
# Annonces à ignorer : cassées / pour pièces / boîtes (vides) / bloquées iCloud
# (multilingue : FR/EN/IT/ES/PT/DE — Vinted est paneuropéen).
_DEAL_SKIP_RE = re.compile(
    r"\b(hs|cass[ée]e?|pour ?pi[èe]ces|d[ée]fectueux|defekt|da riparare|broken|"
    r"not working|ne fonctionne|en panne|faulty|kaputt|rotto|guasto|averiado|"
    r"r[ée]paration|repair|"
    r"bo[îi]te ?vide|boite|caja|caixa|scatola|vuota|leer|leere|ovp|"
    r"icloud|verrouill|locked|bloqu|blacklist|blocc)\b",
    re.IGNORECASE,
)

CONCURRENCY = int(os.environ.get("CONCURRENCY", "4"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "20"))

# Sorties.
REPORT_JSON = os.environ.get("REPORT_JSON", "state/vinted_report.json")
REPORT_CSV = os.environ.get("REPORT_CSV", "state/vinted_report.csv")
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"

# Mode boucle : relance l'analyse en continu toutes les LOOP_INTERVAL_SECONDS,
# pendant au plus LOOP_MAX_SECONDS (0 = illimité, idéal en service systemd).
LOOP_ENABLED = os.environ.get("LOOP_ENABLED", "false").lower() == "true"
LOOP_INTERVAL_SECONDS = int(os.environ.get("LOOP_INTERVAL_SECONDS", "3600"))
LOOP_MAX_SECONDS = int(os.environ.get("LOOP_MAX_SECONDS", "0"))

# Notifications (réutilise la logique du watcher Micromania).
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
USER_AGENT_BOT = "VintedDemandBot (https://github.com, 1.0)"


# --------------------------------------------------------------------------- #
# Watchdog systemd (heartbeat sd_notify, no-op hors systemd)
# --------------------------------------------------------------------------- #

def sd_notify(state: str) -> None:
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    try:
        if addr.startswith("@"):
            addr = "\0" + addr[1:]
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        s.connect(addr)
        s.sendall(state.encode())
        s.close()
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# HTTP — Vinted est protégé par DataDome. Depuis une IP datacenter (VPS), les
# requêtes nues (urllib) sont bloquées en 403. On imite l'empreinte TLS de
# Chrome via curl_cffi et on réchauffe la session par un GET de la home (pose
# les cookies de session anonyme nécessaires à l'API). Repli urllib sinon.
# --------------------------------------------------------------------------- #

IMPERSONATE = os.environ.get("IMPERSONATE", "chrome")
PROXY = os.environ.get("PROXY", "").strip()
_PROXIES = {"http": PROXY, "https": PROXY} if PROXY else None

try:
    from curl_cffi import requests as cffi  # type: ignore

    HAVE_CFFI = True
except Exception:  # noqa: BLE001
    HAVE_CFFI = False

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fr-FR,fr;q=0.9",
}
# Session connectée facultative : débloque les VUES (sinon API anonyme).
if VINTED_COOKIE:
    _BROWSER_HEADERS["Cookie"] = VINTED_COOKIE
if VINTED_ACCESS_TOKEN:
    _BROWSER_HEADERS["Authorization"] = f"Bearer {VINTED_ACCESS_TOKEN}"

_tls = threading.local()
_warm_logged = [False]
_warm_lock = threading.Lock()


def _is_challenge(content: bytes) -> bool:
    """Détecte une page-challenge anti-bot (DataDome).

    Attention : la home Vinted est du HTML légitime — on ne flague donc PAS le
    simple fait d'être du HTML, seulement les marqueurs explicites de challenge
    ou une réponse anormalement courte.
    """
    if len(content) < 2:
        return True
    head = content[:1500].lstrip().lower()
    return (
        b"datadome" in head
        or b"captcha-delivery" in head
        or b"are you a human" in head
    )


def _new_session():
    s = cffi.Session(impersonate=IMPERSONATE, proxies=_PROXIES)
    try:
        s.headers.update(_BROWSER_HEADERS)
    except Exception:  # noqa: BLE001
        pass
    # Warm-up : GET home -> pose les cookies de session anonyme (sans eux,
    # l'API renvoie 401/403).
    try:
        s.get(SITE_ROOT + "/", timeout=REQUEST_TIMEOUT)
    except Exception:  # noqa: BLE001
        pass
    with _warm_lock:
        if not _warm_logged[0]:
            _warm_logged[0] = True
            print("[warmup] session curl_cffi réchauffée (home Vinted)")
    return s


def _session():
    s = getattr(_tls, "s", None)
    if s is None:
        s = _new_session()
        _tls.s = s
    return s


# Limiteur de débit adaptatif : ralentit en cas de blocage, ré-accélère quand
# tout va bien (même logique que le watcher Micromania).
RATE_MIN = float(os.environ.get("RATE_MIN", "0.3"))
RATE_MAX = float(os.environ.get("RATE_MAX", "5.0"))
RATE_START = float(os.environ.get("RATE_START", "0.6"))
_rate_lock = threading.Lock()
_last_req = [0.0]
_interval = [RATE_START]
_ok_streak = [0]


def _rate_gate() -> None:
    with _rate_lock:
        now = time.monotonic()
        target = max(now, _last_req[0] + _interval[0])
        _last_req[0] = target
    delay = target - time.monotonic()
    if delay > 0:
        time.sleep(delay)


def _note_block() -> None:
    with _rate_lock:
        _ok_streak[0] = 0
        old = _interval[0]
        _interval[0] = min(_interval[0] * 1.6, RATE_MAX)
        if _interval[0] != old:
            print(
                f"[auto-débit] ralentissement → {_interval[0]:.2f}s/requête",
                file=sys.stderr,
            )


def _note_ok() -> None:
    with _rate_lock:
        _ok_streak[0] += 1
        if _ok_streak[0] >= 40 and _interval[0] > RATE_MIN:
            _ok_streak[0] = 0
            _interval[0] = max(_interval[0] * 0.9, RATE_MIN)


_urllib_opener_holder = [None]
_urllib_lock = threading.Lock()


def _rewarm_urllib(opener) -> None:
    """(Re)pose les cookies de session en visitant la home avec cet opener."""
    try:
        req = urllib.request.Request(SITE_ROOT + "/", headers=_BROWSER_HEADERS)
        with opener.open(req, timeout=REQUEST_TIMEOUT) as resp:
            resp.read()
    except Exception:  # noqa: BLE001
        pass


def _urllib_opener():
    """Opener urllib partagé, avec cookie jar, réchauffé une fois."""
    with _urllib_lock:
        if _urllib_opener_holder[0] is None:
            import http.cookiejar

            jar = http.cookiejar.CookieJar()
            opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(jar)
            )
            _rewarm_urllib(opener)
            _urllib_opener_holder[0] = opener
            print("[warmup] opener urllib réchauffé (home Vinted)")
        return _urllib_opener_holder[0]


def http_get_json(url: str, retries: int = 3):
    """GET d'un endpoint JSON, résistant à DataDome (session réchauffée +
    auto-régulation). Renvoie l'objet décodé ou lève une RuntimeError."""
    raw = http_get(url, retries=retries)
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError as err:
        raise RuntimeError(f"réponse non-JSON pour {url}") from err


def http_get(url: str, retries: int = 3) -> bytes:
    last_err: Exception | None = None

    if HAVE_CFFI:
        for attempt in range(retries):
            try:
                _rate_gate()
                r = _session().get(url, timeout=REQUEST_TIMEOUT)
                if r.status_code in (404, 410):
                    raise RuntimeError(f"GET {url}: HTTP {r.status_code}")
                if r.status_code in (401, 403) or _is_challenge(r.content):
                    _tls.s = None  # session neuve (cookies périmés / challenge)
                    _note_block()
                    last_err = RuntimeError("blocage anti-bot / session")
                    time.sleep(1.0 * (attempt + 1))
                    continue
                if r.status_code >= 400:
                    last_err = RuntimeError(f"HTTP {r.status_code}")
                    time.sleep(1.0 * (attempt + 1))
                    continue
                _note_ok()
                return r.content
            except RuntimeError:
                raise
            except Exception as err:  # noqa: BLE001
                _tls.s = None
                _note_block()
                last_err = err
                time.sleep(1.0 * (attempt + 1))
        raise RuntimeError(f"GET échoué pour {url}: {last_err}")

    # --- Repli urllib (IP non bloquée) : l'API Vinted exige le cookie de
    # session anonyme -> on utilise un opener AVEC cookie jar, réchauffé par un
    # GET de la home (qui pose les cookies access_token_web / _vinted_*_session).
    opener = _urllib_opener()
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={**_BROWSER_HEADERS, "Accept-Encoding": "gzip"}
            )
            with opener.open(req, timeout=REQUEST_TIMEOUT) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                if _is_challenge(raw):
                    raise urllib.error.HTTPError(url, 403, "challenge", resp.headers, None)
                return raw
        except urllib.error.HTTPError as err:
            if err.code in (404, 410):
                raise RuntimeError(f"GET {url}: HTTP {err.code}") from err
            if err.code in (401, 403):
                _rewarm_urllib(opener)  # cookies périmés -> on re-réchauffe
            last_err = err
            time.sleep(1.5 * (attempt + 1))
        except Exception as err:  # noqa: BLE001
            last_err = err
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET échoué pour {url}: {last_err}")


# --------------------------------------------------------------------------- #
# Recherche par IMAGE (nécessite une session CONNECTÉE via VINTED_COOKIE)
#   1) POST /web/api/images/images (multipart stream + photo_type=query_image)
#   2) GET  /api/v2/catalog/items?search_by_image_id=<id>
# --------------------------------------------------------------------------- #

IMAGE_UPLOAD_URL = os.environ.get(
    "IMAGE_UPLOAD_URL", f"{SITE_ROOT}/web/api/images/images"
)
# Le token est sérialisé (souvent ÉCHAPPÉ) dans le payload Next.js de la home :
#   \"CSRF_TOKEN\":\"<uuid>\"
_CSRF_RE = re.compile(r'\\?"CSRF_TOKEN\\?":\\?"([^"\\]+)')
_CSRF_META_RE = re.compile(
    r'<meta[^>]+name=["\']csrf-token["\'][^>]+content=["\']([^"\']+)', re.I
)


def _csrf_token() -> str:
    """Récupère le CSRF token depuis la home (session connectée)."""
    try:
        html_txt = http_get(SITE_ROOT + "/").decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return ""
    m = _CSRF_RE.search(html_txt) or _CSRF_META_RE.search(html_txt)
    return m.group(1) if m else ""


def _multipart_image(image_bytes: bytes, filename: str = "query.jpg") -> tuple[bytes, str]:
    """Construit un corps multipart (champ 'stream' + photo_type=query_image)."""
    import uuid as _uuid
    boundary = "----vinted" + _uuid.uuid4().hex
    pre = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="stream"; filename="{filename}"\r\n'
        f"Content-Type: image/jpeg\r\n\r\n"
    ).encode()
    mid = (
        f"\r\n--{boundary}\r\n"
        f'Content-Disposition: form-data; name="photo_type"\r\n\r\nquery_image\r\n'
        f"--{boundary}--\r\n"
    ).encode()
    return pre + image_bytes + mid, boundary


def upload_query_image(image_bytes: bytes) -> str:
    """Upload l'image et renvoie son id (recherche par image). Lève une erreur
    explicite si la session n'est pas connectée."""
    if not (VINTED_COOKIE or VINTED_ACCESS_TOKEN):
        raise RuntimeError(
            "Recherche par image : session connectée requise. Renseigne "
            "VINTED_COOKIE (cookie de ton compte Vinted)."
        )
    body, boundary = _multipart_image(image_bytes)
    csrf = _csrf_token()
    headers = {
        **_BROWSER_HEADERS,
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Accept": "application/json",
        "Referer": SITE_ROOT + "/",
    }
    if csrf:
        headers["X-Csrf-Token"] = csrf

    raw = b""
    if HAVE_CFFI:
        r = _session().post(IMAGE_UPLOAD_URL, data=body, headers=headers,
                            timeout=REQUEST_TIMEOUT)
        if r.status_code >= 400:
            raise RuntimeError(f"upload image: HTTP {r.status_code} {r.text[:200]}")
        raw = r.content
    else:
        opener = _urllib_opener()
        req = urllib.request.Request(IMAGE_UPLOAD_URL, data=body, headers=headers,
                                     method="POST")
        try:
            with opener.open(req, timeout=REQUEST_TIMEOUT) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as err:
            raise RuntimeError(
                f"upload image: HTTP {err.code} {err.read()[:200]!r}"
            ) from err
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError as err:
        raise RuntimeError(f"upload image: réponse non-JSON ({raw[:120]!r})") from err
    iid = data.get("id") or (data.get("photo") or {}).get("id") or data.get("image_id")
    if not iid:
        raise RuntimeError(f"upload image: id introuvable dans {data}")
    return str(iid)


def search_by_image(image_bytes: bytes, max_pages: int = 2) -> list[dict]:
    """Recherche les articles Vinted ressemblant à l'image fournie."""
    image_id = upload_query_image(image_bytes)
    items = _fetch_items({"search_by_image_id": image_id}, max_pages,
                         query="📷 image")
    return items


# --------------------------------------------------------------------------- #
# Client API Vinted
# --------------------------------------------------------------------------- #

def _price(item: dict) -> float | None:
    """Prix vendeur d'un article (gère le format objet et le format plat)."""
    p = item.get("price")
    if isinstance(p, dict):
        amt = p.get("amount")
        return float(amt) if amt is not None else None
    if p is None:
        return None
    try:
        return float(str(p).replace(",", "."))
    except ValueError:
        return None


def _photo_url(item: dict) -> str:
    photo = item.get("photo") or {}
    if isinstance(photo, dict):
        return photo.get("url") or (photo.get("thumbnails") or [{}])[0].get("url", "")
    return ""


# Heure de RÉFÉRENCE pour calculer l'âge = horloge du serveur Vinted
# (pagination.time de chaque réponse). Fiable même si l'horloge locale dérive
# (utile en CI). Repli sur time.time() tant qu'aucune réponse n'a été lue.
_server_now = [0.0]
_server_lock = threading.Lock()


def _ref_now() -> float:
    with _server_lock:
        return _server_now[0] or time.time()


def _note_server_time(data: dict) -> None:
    t = (data.get("pagination") or {}).get("time")
    if t:
        with _server_lock:
            if t > _server_now[0]:
                _server_now[0] = float(t)


def _posted_ts(item: dict) -> int | None:
    """Timestamp (Unix) de mise en ligne ≈ date de publication, lu sur la
    photo principale (photo.high_resolution.timestamp)."""
    photo = item.get("photo") or {}
    if isinstance(photo, dict):
        hr = photo.get("high_resolution") or {}
        ts = hr.get("timestamp")
        if ts:
            try:
                return int(ts)
            except (TypeError, ValueError):
                return None
    return None


def _normalize(it: dict, *, query: str = "", category: str = "") -> dict:
    """Article brut de l'API -> dict normalisé commun à tous les modes."""
    ts = _posted_ts(it)
    age = None
    if ts:
        age = round((_ref_now() - ts) / 86400.0, 2)
    return {
        "id": it.get("id"),
        "title": it.get("title") or "",
        "price": _price(it),
        "brand": it.get("brand_title") or "",
        "size": it.get("size_title") or "",
        "status": it.get("status") or "",
        "favourites": int(it.get("favourite_count") or 0),
        "views": None,  # rempli par fetch_views (option, session requise)
        "posted_ts": ts,
        "age_days": age,
        "url": it.get("url") or f"{SITE_ROOT}/items/{it.get('id')}",
        "image": _photo_url(it),
        "query": query,
        "category": category,
    }


def _fetch_items(extra_params: dict, max_pages: int, *, query: str = "",
                 category: str = "", age_max_days: float | None = None,
                 order: str | None = None) -> list[dict]:
    """Lit jusqu'à max_pages du catalogue avec les filtres donnés.

    Si age_max_days est fixé, on s'arrête dès qu'une page n'apporte plus
    d'article assez récent (l'API trie en gros par fraîcheur/pertinence), et on
    ne renvoie que les articles dans la fenêtre.
    """
    items: list[dict] = []
    seen_ids: set = set()
    for page in range(1, max_pages + 1):
        params = {
            "page": page,
            "per_page": PER_PAGE,
            "order": order or CATALOG_ORDER,
            "currency": CURRENCY,
            **extra_params,
        }
        if PRICE_FROM:
            params["price_from"] = PRICE_FROM
        if PRICE_TO:
            params["price_to"] = PRICE_TO
        url = f"{API_ROOT}/catalog/items?" + urllib.parse.urlencode(params)
        data = http_get_json(url)
        _note_server_time(data)  # cale l'heure de référence sur le serveur
        raw_items = data.get("items") or []
        fresh_on_page = 0
        for it in raw_items:
            iid = it.get("id")
            if iid in seen_ids:
                continue
            seen_ids.add(iid)
            norm = _normalize(it, query=query, category=category)
            if age_max_days is not None:
                if norm["age_days"] is None or norm["age_days"] > age_max_days:
                    continue  # hors fenêtre de fraîcheur
                fresh_on_page += 1
            items.append(norm)
        pag = data.get("pagination") or {}
        total_pages = int(pag.get("total_pages") or 0)
        if not raw_items or (total_pages and page >= total_pages):
            break
        # En mode fenêtre : si une page entière ne contient plus rien de récent,
        # inutile de continuer (les suivantes sont encore plus anciennes).
        if age_max_days is not None and fresh_on_page == 0 and page >= 2:
            break
    return items


def fetch_catalog(query: str) -> list[dict]:
    """Lit le catalogue pour une recherche texte (mode watchlist)."""
    return _fetch_items({"search_text": query}, MAX_PAGES, query=query)


def fetch_views(item_id) -> tuple[int | None, int | None]:
    """Lit (vues, favoris) sur la fiche d'un article. Renvoie (None, None) en
    cas d'échec. Les favoris de la fiche sont plus à jour que ceux du listing."""
    try:
        data = http_get_json(f"{API_ROOT}/items/{item_id}")
    except Exception:  # noqa: BLE001
        return None, None
    it = data.get("item") or {}
    views = it.get("view_count")
    favs = it.get("favourite_count")
    return (
        int(views) if views is not None else None,
        int(favs) if favs is not None else None,
    )


# --------------------------------------------------------------------------- #
# Arbre des catégories Vinted (mode scan)
# --------------------------------------------------------------------------- #

def fetch_catalog_tree() -> list[dict]:
    """Récupère l'arbre des catégories depuis la home (JSON embarqué).

    Vinted n'expose pas d'endpoint catégories ; l'arbre est sérialisé dans la
    page d'accueil sous la clé "catalogTree". On le déséchappe et on le parse.
    """
    raw = http_get(SITE_ROOT + "/").decode("utf-8", "replace")
    s = raw.replace('\\"', '"').replace("\\\\", "\\")
    key = '"catalogTree":'
    i = s.find(key)
    if i == -1:
        raise RuntimeError("catalogTree introuvable dans la home")
    j = s.find("[", i)
    depth = 0
    end = None
    for k in range(j, len(s)):
        c = s[k]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                end = k + 1
                break
    if end is None:
        raise RuntimeError("catalogTree mal formé")
    return json.loads(s[j:end])


def _strip_accents(text: str) -> str:
    table = str.maketrans("àâäéèêëïîôöùûüç", "aaaeeeeiioouuuc")
    return text.lower().translate(table)


def _excluded(title: str) -> bool:
    t = _strip_accents(title)
    return any(_strip_accents(p) in t for p in EXCLUDE_PATTERNS)


def list_scan_categories() -> list[dict]:
    """Liste des catégories à scanner = sous-catégories de niveau 1 de chaque
    racine, en retirant les vêtements (EXCLUDE_PATTERNS). Renvoie [{id, title}].

    Si VINTED_CATEGORIES est fourni, on l'utilise tel quel (override).
    """
    if VINTED_CATEGORIES:
        return [{"id": cid, "title": f"cat {cid}"} for cid in VINTED_CATEGORIES]

    tree = fetch_catalog_tree()
    cats: list[dict] = []
    seen: set = set()
    for root in tree:
        if _excluded(root.get("title", "")):
            continue
        children = root.get("catalogs") or []
        # Une racine sans enfants -> on la scanne directement.
        targets = children or [root]
        for c in targets:
            title = c.get("title", "")
            cid = c.get("id")
            if cid is None or cid in seen or _excluded(title):
                continue
            seen.add(cid)
            cats.append({"id": cid, "title": f"{root.get('title','')} › {title}"})
    return cats


def scan_category(cat: dict) -> list[dict]:
    """Scanne une catégorie : articles postés depuis DAYS_WINDOW jours."""
    try:
        items = _fetch_items(
            {"catalog_ids": cat["id"]},
            CATEGORY_MAX_PAGES,
            category=cat["title"],
            age_max_days=DAYS_WINDOW,
            order="relevance",  # remonte les articles récents les + engageants
        )
    except Exception as err:  # noqa: BLE001
        print(f"[catégorie] {cat['title']}: {err}", file=sys.stderr)
        return []
    return items


# --------------------------------------------------------------------------- #
# Analyse / score de demande
# --------------------------------------------------------------------------- #

def hotness(item: dict) -> float:
    """Favoris par jour depuis la mise en ligne : favorise ce qui monte vite."""
    age = item.get("age_days")
    fav = item.get("favourites") or 0
    if not age or age < 0.5:
        age = 0.5  # évite de surévaluer les articles de quelques heures
    return round(fav / age, 1)


def rank_key(item: dict) -> float:
    """Clé de tri du top selon RANK_BY (hotness = le plus frais qui monte)."""
    if RANK_BY == "favourites":
        return item.get("favourites") or 0
    return hotness(item)


def demand_score(item: dict) -> float:
    """Score de demande d'un article : favoris + poids · vues."""
    fav = item.get("favourites") or 0
    views = item.get("views") or 0
    return fav + VIEW_WEIGHT * views


def _median(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None]
    return round(statistics.median(vals), 2) if vals else None


def _mean(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None]
    return round(statistics.fmean(vals), 2) if vals else None


def get_total_listings(query: str) -> int | None:
    """Nombre TOTAL d'annonces actives sur Vinted pour ce mot-clé (l'offre /
    la concurrence), lu via pagination.total_entries."""
    params = {"search_text": query, "page": 1, "per_page": 1, "currency": CURRENCY}
    if PRICE_FROM:
        params["price_from"] = PRICE_FROM
    if PRICE_TO:
        params["price_to"] = PRICE_TO
    try:
        data = http_get_json(f"{API_ROOT}/catalog/items?" + urllib.parse.urlencode(params))
        return int((data.get("pagination") or {}).get("total_entries") or 0)
    except Exception:  # noqa: BLE001
        return None


def _percentile(sorted_vals: list[float], p: float) -> float | None:
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    return round(sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f), 2)


def estimate_velocity(query: str) -> float | None:
    """Estime le RYTHME D'ÉCOULEMENT : nombre d'annonces postées par jour,
    calculé sur les annonces les plus récentes. À l'équilibre, le flux d'entrée
    ≈ le flux de vente, donc c'est un bon proxy de « combien il s'en vend/jour »."""
    try:
        items = _fetch_items({"search_text": query}, 1, order="newest_first")
    except Exception:  # noqa: BLE001
        return None
    ts = sorted(it["posted_ts"] for it in items if it.get("posted_ts"))
    if len(ts) < 10:
        return None
    span_days = (ts[-1] - ts[0]) / 86400.0
    if span_days <= 0:
        return None
    return round(len(ts) / span_days, 1)


def resale_breakdown(avg_fav: float, velocity: float | None,
                     pct_hot: float) -> dict:
    """Détail du score de revente : sous-notes Demande/Écoulement/Ampleur."""
    demand = round(min((avg_fav or 0) * 3.5, 60))
    speed = round(min((velocity or 0) * 1.5, 30)) if velocity else 0
    breadth = round(min(pct_hot * 0.2, 10))
    return {"demand": demand, "speed": speed, "breadth": breadth,
            "total": min(demand + speed + breadth, 100)}


def resale_score(avg_fav: float, velocity: float | None, pct_hot: float) -> int:
    """Score de revente 0-100 : demande (favoris) + écoulement + ampleur."""
    return int(resale_breakdown(avg_fav, velocity, pct_hot)["total"])


def advice_revente(r: dict) -> str:
    """Phrase de conseil concrète selon demande / écoulement / concurrence."""
    avg = r.get("avg_favourites") or 0
    vel = r.get("velocity")
    n = r.get("n_total") or 0
    demand_ok = avg >= 8
    fast = (vel or 0) >= 8
    if demand_ok and fast:
        return ("✅ Très recherché ET part vite : revente facile et rapide. "
                "Sous le prix médian, fonce.")
    if demand_ok and not fast:
        return ("🟡 Recherché mais s'écoule lentement (offre abondante) : marge "
                "possible, mais la revente peut prendre des semaines.")
    if not demand_ok and fast:
        return ("🟡 Ça tourne vite mais peu de favoris : se vend surtout au bon "
                "prix, marge plus serrée.")
    if n and n < 30:
        return ("🔵 Niche : peu d'offre et peu de demande. Rentable surtout si tu "
                "as déjà un acheteur ou une pièce rare.")
    if avg >= 4:
        return ("🟡 Demande modérée et écoulement plutôt lent : revente possible "
                "mais sans garantie de rapidité — achète au bas du marché.")
    return ("🔴 Peu recherché et peu d'écoulement : risque de rester sur les bras, "
            "à éviter pour de la revente.")


def verdict_revente(n_total: int | None, avg_fav: float | None,
                    n_sample: int) -> dict:
    """Verdict achat-revente à partir de la demande (favoris/annonce) et de
    l'offre (nombre d'annonces). favoris/annonce = appétit des acheteurs."""
    a = avg_fav or 0
    if a >= 10:
        emoji, label = "🟢", "ACHÈTE — très recherché"
    elif a >= 4:
        emoji, label = "🟡", "PRUDENCE — demande moyenne"
    else:
        emoji, label = "🔴", "ÉVITE — peu recherché"
    # Nuances sur l'offre.
    note = ""
    if n_total is not None:
        if n_total < 30:
            note = "offre rare (niche, peu de concurrence)"
        elif n_total >= 950:
            note = "offre très large (beaucoup de concurrence)"
        else:
            note = "marché liquide"
    return {"emoji": emoji, "label": label, "avg_fav": round(a, 1), "note": note}


def _fmt_total(n: int | None) -> str:
    """Affiche le nombre d'annonces (Vinted plafonne le compteur à ~960)."""
    if n is None:
        return "—"
    return "900+" if n >= 950 else str(n)


def analyze_query(query: str) -> dict | None:
    """Scanne une recherche et calcule ses indicateurs de demande."""
    try:
        items = fetch_catalog(query)
    except Exception as err:  # noqa: BLE001
        print(f"[recherche] {query!r}: {err}", file=sys.stderr)
        return None
    if not items:
        print(f"[recherche] {query!r}: aucun article trouvé")
        return None

    # Enrichissement des VUES sur les meilleurs articles (par favoris) seulement.
    # Inutile sans session : l'API renvoie 404 en anonyme sur la fiche.
    if FETCH_VIEWS and TOP_VIEWS > 0 and (VINTED_COOKIE or VINTED_ACCESS_TOKEN):
        top = sorted(items, key=lambda i: i["favourites"], reverse=True)[:TOP_VIEWS]
        with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
            futs = {pool.submit(fetch_views, it["id"]): it for it in top}
            for fut in as_completed(futs):
                it = futs[fut]
                views, favs = fut.result()
                if views is not None:
                    it["views"] = views
                if favs is not None:
                    it["favourites"] = favs

    for it in items:
        it["score"] = round(demand_score(it), 2)

    # FILTRE DE PERTINENCE : on ne garde que les annonces dont le TITRE contient
    # vraiment tous tes mots-clés (≥3 lettres), pour coller à TON produit et pas
    # au bruit de la recherche. Repli sur tout si trop peu d'annonces matchent.
    qtokens = [
        t for t in re.findall(r"[a-z0-9]+", _strip_accents(query.lower()))
        if len(t) >= 3 and t not in _DEAL_STOP
    ]

    def _relevant(it: dict) -> bool:
        if not qtokens:
            return True
        t = _strip_accents((it.get("title") or "").lower())
        return all(tok in t for tok in qtokens)

    matched = [it for it in items if _relevant(it)]
    match_pct = round(100 * len(matched) / len(items)) if items else 0
    core = matched if len(matched) >= 10 else items  # repli si trop peu matchent

    prices = [i["price"] for i in core]
    favs = [i["favourites"] for i in core]
    views_known = [i["views"] for i in core if i["views"] is not None]
    ranked = sorted(core, key=lambda i: i["score"], reverse=True)
    avg_fav = _mean(favs)
    n_total = get_total_listings(query)
    sp = sorted(p for p in prices if p is not None and p > 0)
    pct_hot = round(100 * sum(1 for f in favs if f > 10) / len(favs)) if favs else 0
    velocity = estimate_velocity(query)
    p25 = _percentile(sp, 0.25)
    median_price = _median(prices)
    # Répartition par état (neuf / très bon / bon…), prix médian par état.
    conds: dict[str, list] = {}
    for it in core:
        c = (it.get("status") or "").strip()
        if c and it.get("price"):
            conds.setdefault(c, []).append(it["price"])
    conditions = {
        c: {"n": len(v), "median": round(statistics.median(v), 2)}
        for c, v in sorted(conds.items(), key=lambda kv: -len(kv[1]))
    }

    parts = resale_breakdown(avg_fav or 0, velocity, pct_hot)
    result = {
        "query": query,
        "n_total": n_total,             # offre réelle sur Vinted
        "n_listings": len(core),        # annonces analysées (pertinentes)
        "n_scanned": len(items),        # annonces ramenées par la recherche
        "match_pct": match_pct,         # % qui contiennent vraiment tes mots-clés
        "strict_match": len(matched) >= 10,
        "total_favourites": sum(favs),
        "avg_favourites": avg_fav,
        "max_favourites": max(favs) if favs else 0,
        "with_fav_pct": round(100 * sum(1 for f in favs if f > 0) / len(favs)) if favs else 0,
        "pct_hot": pct_hot,             # % d'annonces à fort intérêt (>10 ❤)
        "velocity": velocity,           # ≈ annonces postées/jour (rythme de vente)
        "avg_views": _mean(views_known) if views_known else None,
        "total_views": sum(views_known) if views_known else None,
        "median_price": median_price,
        "p25_price": p25,               # prix d'achat « malin »
        "p75_price": _percentile(sp, 0.75),
        "min_price": round(sp[0], 2) if sp else None,
        "max_price": round(sp[-1], 2) if sp else None,
        "margin": round(median_price - p25, 2) if (median_price and p25) else None,
        "conditions": conditions,
        "demand_index": round(_mean([i["score"] for i in items]) or 0, 2),
        "score": parts["total"],
        "score_parts": parts,
        "verdict": verdict_revente(n_total, avg_fav, len(items)),
        "top_items": ranked[: max(TOP_VIEWS, 10)],
        "all_items": items,
    }
    result["advice"] = advice_revente(result)
    return result


def velocity_from_items(items: list[dict]) -> float | None:
    """Rythme d'écoulement estimé à partir des dates des articles fournis."""
    ts = sorted(it["posted_ts"] for it in items if it.get("posted_ts"))
    if len(ts) < 8:
        return None
    span = (ts[-1] - ts[0]) / 86400.0
    return round(len(ts) / span, 1) if span > 0 else None


def analyze_image(image_bytes: bytes, label: str = "📷 Recherche par image") -> dict | None:
    """Analyse de revente à partir d'une IMAGE (mêmes indicateurs que par texte)."""
    items = search_by_image(image_bytes)
    if not items:
        return None
    for it in items:
        it["score"] = round(demand_score(it), 2)
    prices = [i["price"] for i in items]
    favs = [i["favourites"] for i in items]
    sp = sorted(p for p in prices if p is not None and p > 0)
    avg_fav = _mean(favs)
    pct_hot = round(100 * sum(1 for f in favs if f > 10) / len(favs)) if favs else 0
    velocity = velocity_from_items(items)
    p25 = _percentile(sp, 0.25)
    median_price = _median(prices)
    conds: dict[str, list] = {}
    for it in items:
        c = (it.get("status") or "").strip()
        if c and it.get("price"):
            conds.setdefault(c, []).append(it["price"])
    conditions = {
        c: {"n": len(v), "median": round(statistics.median(v), 2)}
        for c, v in sorted(conds.items(), key=lambda kv: -len(kv[1]))
    }
    parts = resale_breakdown(avg_fav or 0, velocity, pct_hot)
    titles = ", ".join(dict.fromkeys(
        (it.get("title") or "").split(" - ")[0][:24] for it in
        sorted(items, key=lambda i: i["favourites"], reverse=True)[:3]
    ))
    result = {
        "query": f"{label} ({titles})" if titles else label,
        "n_total": len(items), "n_listings": len(items), "n_scanned": len(items),
        "match_pct": 100, "strict_match": True,
        "total_favourites": sum(favs), "avg_favourites": avg_fav,
        "max_favourites": max(favs) if favs else 0,
        "with_fav_pct": round(100 * sum(1 for f in favs if f > 0) / len(favs)) if favs else 0,
        "pct_hot": pct_hot, "velocity": velocity,
        "avg_views": None, "total_views": None,
        "median_price": median_price, "p25_price": p25, "p75_price": _percentile(sp, 0.75),
        "min_price": round(sp[0], 2) if sp else None,
        "max_price": round(sp[-1], 2) if sp else None,
        "margin": round(median_price - p25, 2) if (median_price and p25) else None,
        "conditions": conditions, "demand_index": round(avg_fav or 0, 2),
        "score": parts["total"], "score_parts": parts,
        "verdict": verdict_revente(len(items), avg_fav, len(items)),
        "top_items": sorted(items, key=lambda i: i["favourites"], reverse=True)[:10],
        "all_items": items,
    }
    result["advice"] = advice_revente(result)
    return result


# --------------------------------------------------------------------------- #
# Rapport
# --------------------------------------------------------------------------- #

def _euro(x: float | None) -> str:
    return "—" if x is None else f"{x:.2f}".replace(".", ",") + " €"


def _fmt_int(x) -> str:
    return "—" if x is None else f"{int(x)}"


def print_report(results: list[dict]) -> None:
    """Affiche le classement dans la console."""
    ranked = sorted(results, key=lambda r: r["demand_index"], reverse=True)
    print("\n" + "=" * 78)
    print("CLASSEMENT DE LA DEMANDE VINTED (le plus recherché en premier)")
    print("Score = favoris + {:.2f}·vues  | prix = médiane des annonces".format(VIEW_WEIGHT))
    print("=" * 78)
    header = f"{'#':>2}  {'recherche':<26}{'demande':>9}{'fav.moy':>9}{'vues.moy':>9}{'annonces':>9}{'prix méd':>11}"
    print(header)
    print("-" * 78)
    for i, r in enumerate(ranked, 1):
        print(
            f"{i:>2}  {r['query'][:26]:<26}"
            f"{r['demand_index']:>9.1f}"
            f"{(r['avg_favourites'] or 0):>9.1f}"
            f"{(_fmt_int(r['avg_views'])):>9}"
            f"{r['n_listings']:>9}"
            f"{_euro(r['median_price']):>11}"
        )

    # Top des articles individuels, tous recherches confondues.
    all_items: list[dict] = []
    for r in results:
        all_items.extend(r["all_items"])
    top = sorted(all_items, key=lambda x: x.get("score", 0), reverse=True)[:TOP_ITEMS]
    print("\n" + "-" * 78)
    print(f"TOP {len(top)} ARTICLES LES PLUS CONVOITÉS (favoris / vues)")
    print("-" * 78)
    for i, it in enumerate(top, 1):
        print(
            f"{i:>2}. ❤{_fmt_int(it['favourites']):>4}  👁{_fmt_int(it['views']):>6}  "
            f"{_euro(it['price']):>9}  {it['title'][:42]:<42}  {it['url']}"
        )
    print("=" * 78 + "\n")


def write_reports(results: list[dict]) -> None:
    """Écrit le rapport en JSON (complet) et CSV (résumé par recherche)."""
    ranked = sorted(results, key=lambda r: r["demand_index"], reverse=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "domain": VINTED_DOMAIN,
        "view_weight": VIEW_WEIGHT,
        "queries": [
            {k: v for k, v in r.items() if k != "all_items"} for r in ranked
        ],
    }
    try:
        os.makedirs(os.path.dirname(REPORT_JSON) or ".", exist_ok=True)
        with open(REPORT_JSON, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print(f"[rapport] JSON écrit -> {REPORT_JSON}")
    except OSError as err:
        print(f"[rapport] JSON échec: {err}", file=sys.stderr)

    try:
        os.makedirs(os.path.dirname(REPORT_CSV) or ".", exist_ok=True)
        with open(REPORT_CSV, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(
                [
                    "rang", "recherche", "indice_demande", "favoris_moyens",
                    "vues_moyennes", "annonces", "prix_median", "prix_min",
                    "favoris_total", "vues_total",
                ]
            )
            for i, r in enumerate(ranked, 1):
                w.writerow(
                    [
                        i, r["query"], r["demand_index"], r["avg_favourites"],
                        r["avg_views"], r["n_listings"], r["median_price"],
                        r["min_price"], r["total_favourites"], r["total_views"],
                    ]
                )
        print(f"[rapport] CSV écrit -> {REPORT_CSV}")
    except OSError as err:
        print(f"[rapport] CSV échec: {err}", file=sys.stderr)


# --------------------------------------------------------------------------- #
# Notifications (digest Discord / Telegram)
# --------------------------------------------------------------------------- #

def _http_post_json(url: str, payload: dict) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT_BOT},
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        resp.read()


def _digest_lines(results: list[dict], limit: int = 15) -> list[str]:
    ranked = sorted(results, key=lambda r: r["demand_index"], reverse=True)[:limit]
    lines = []
    for i, r in enumerate(ranked, 1):
        views = "" if r["avg_views"] is None else f" · 👁{int(r['avg_views'])}/art."
        lines.append(
            f"**{i}. {r['query']}** — demande {r['demand_index']:.0f} "
            f"(❤{(r['avg_favourites'] or 0):.0f}/art.{views}) · "
            f"prix méd. {_euro(r['median_price'])} · {r['n_listings']} annonces"
        )
    return lines


def send_digest(results: list[dict]) -> None:
    if not results:
        return
    title = "🛍️ Demande Vinted — le plus recherché"
    body = "\n".join(_digest_lines(results))
    if DRY_RUN:
        print("[DRY_RUN] digest:\n" + title + "\n" + body)
        return

    if DISCORD_WEBHOOK_URL:
        embed = {
            "title": title,
            "description": body[:4000],
            "color": 0x09B1BA,  # turquoise Vinted
            "footer": {"text": "Vinted demand analyzer"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        try:
            _http_post_json(DISCORD_WEBHOOK_URL, {"embeds": [embed]})
        except Exception as err:  # noqa: BLE001
            print(f"[discord] échec: {err}", file=sys.stderr)

    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        text = title + "\n\n" + body.replace("**", "")
        try:
            _http_post_json(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": text[:4000],
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
            )
        except Exception as err:  # noqa: BLE001
            print(f"[telegram] échec: {err}", file=sys.stderr)


# --------------------------------------------------------------------------- #
# Watchlist
# --------------------------------------------------------------------------- #

def load_queries() -> list[str]:
    """Construit la liste des recherches depuis VINTED_QUERIES puis le fichier."""
    queries: list[str] = []
    if VINTED_QUERIES:
        for chunk in VINTED_QUERIES.replace("\n", ",").split(","):
            q = chunk.strip()
            if q:
                queries.append(q)
    if not queries and os.path.exists(WATCHLIST_FILE):
        with open(WATCHLIST_FILE, "r", encoding="utf-8") as fh:
            for line in fh:
                q = line.split("#", 1)[0].strip()
                if q:
                    queries.append(q)
    # Dédup en gardant l'ordre.
    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        key = q.lower()
        if key not in seen:
            seen.add(key)
            out.append(q)
    return out


# --------------------------------------------------------------------------- #
# Mode SCAN CATÉGORIES : rapport "produits les plus likés (récents)"
# --------------------------------------------------------------------------- #

def _age_label(age: float | None) -> str:
    if age is None:
        return "?"
    if age < 1:
        return f"{int(age * 24)}h"
    return f"{age:.1f}j"


def group_by_category(items: list[dict]) -> list[tuple[str, list[dict]]]:
    """Groupe les articles par catégorie, garde les TOP_PER_CATEGORY meilleurs
    de chaque (triés par rank_key), et ordonne les catégories par leur meilleur
    article (la catégorie la plus chaude d'abord)."""
    groups: dict[str, list[dict]] = {}
    for it in items:
        groups.setdefault(it.get("category") or "?", []).append(it)
    out: list[tuple[str, list[dict]]] = []
    for cat, lst in groups.items():
        lst.sort(key=rank_key, reverse=True)
        out.append((cat, lst[:TOP_PER_CATEGORY]))
    out.sort(key=lambda g: rank_key(g[1][0]) if g[1] else 0, reverse=True)
    return out


def _item_line(rank: int, it: dict) -> str:
    return (
        f"**{rank}.** {hotness(it):.0f} fav/j · ❤{it['favourites']} · "
        f"{_age_label(it['age_days'])} · {_euro(it['price'])} — "
        f"[{it['title'][:42]}]({it['url']})"
    )


def print_products_report(grouped: list[tuple[str, list[dict]]],
                          cats: list[dict]) -> None:
    """Affiche, par catégorie, ses TOP_PER_CATEGORY articles qui montent."""
    crit = "favoris/jour (le + frais)" if RANK_BY != "favourites" else "favoris"
    print("\n" + "=" * 92)
    print(
        f"{TOP_PER_CATEGORY} PRODUITS / CATÉGORIE — postés ≤ {DAYS_WINDOW:.0f}j, "
        f"classés par {crit}, hors vêtements ({len(grouped)} catégories)"
    )
    print("=" * 92)
    for cat, lst in grouped:
        print(f"\n▸ {cat}")
        for i, it in enumerate(lst, 1):
            print(
                f"   {i:>2}. {hotness(it):>5.0f}/j  ❤{it['favourites']:<4} "
                f"{_age_label(it['age_days']):>4}  {_euro(it['price']):>8}  "
                f"{it['title'][:46]:<46}  {it['url']}"
            )
    print("\n" + "=" * 92 + "\n")


def write_products_report(grouped: list[tuple[str, list[dict]]]) -> None:
    """Écrit le rapport groupé par catégorie en JSON + CSV (à plat)."""
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "domain": VINTED_DOMAIN,
        "mode": "categories",
        "days_window": DAYS_WINDOW,
        "rank_by": RANK_BY,
        "per_category": TOP_PER_CATEGORY,
        "categories": [
            {"category": cat, "items": lst} for cat, lst in grouped
        ],
    }
    try:
        os.makedirs(os.path.dirname(REPORT_JSON) or ".", exist_ok=True)
        with open(REPORT_JSON, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print(f"[rapport] JSON écrit -> {REPORT_JSON}")
    except OSError as err:
        print(f"[rapport] JSON échec: {err}", file=sys.stderr)

    try:
        os.makedirs(os.path.dirname(REPORT_CSV) or ".", exist_ok=True)
        with open(REPORT_CSV, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(
                ["categorie", "rang", "favoris_par_jour", "favoris",
                 "age_jours", "prix", "marque", "etat", "titre", "url"]
            )
            for cat, lst in grouped:
                for i, it in enumerate(lst, 1):
                    w.writerow(
                        [cat, i, hotness(it), it["favourites"], it["age_days"],
                         it["price"], it["brand"], it["status"], it["title"],
                         it["url"]]
                    )
        print(f"[rapport] CSV écrit -> {REPORT_CSV}")
    except OSError as err:
        print(f"[rapport] CSV échec: {err}", file=sys.stderr)


def _post_discord_embeds(embeds: list[dict]) -> None:
    """Envoie les embeds par lots (≤10 embeds et ≤5500 car. par message,
    espacés pour respecter le rate-limit du webhook)."""
    batch: list[dict] = []
    size = 0
    sent = 0

    def flush() -> None:
        nonlocal batch, size, sent
        if not batch:
            return
        try:
            _http_post_json(DISCORD_WEBHOOK_URL, {"embeds": batch})
            sent += len(batch)
        except Exception as err:  # noqa: BLE001
            print(f"[discord] échec lot: {err}", file=sys.stderr)
        batch = []
        size = 0
        time.sleep(2.0)  # anti rate-limit (≤30 msg/min/webhook)

    for e in embeds:
        elen = len(e.get("description", "")) + len(e.get("title", ""))
        if batch and (len(batch) >= 8 or size + elen > 5500):
            flush()
        batch.append(e)
        size += elen
    flush()
    print(f"[discord] {sent} embed(s) catégorie envoyés")


def send_products_digest(grouped: list[tuple[str, list[dict]]]) -> None:
    """Un embed Discord PAR catégorie, contenant ses TOP_PER_CATEGORY articles."""
    if not grouped:
        return
    embeds = []
    for cat, lst in grouped:
        if not lst:
            continue
        body = "\n".join(_item_line(i, it) for i, it in enumerate(lst, 1))
        embeds.append(
            {
                "title": f"🔥 {cat}"[:256],
                "description": body[:4000],
                "color": 0x09B1BA,
                "footer": {"text": f"Vinted — postés ≤{DAYS_WINDOW:.0f}j · favoris/jour"},
            }
        )

    if DRY_RUN:
        print(f"[DRY_RUN] {len(embeds)} embeds catégorie (15 articles chacun).")
        for e in embeds[:2]:
            print("\n# " + e["title"])
            print(e["description"])
        return

    if DISCORD_WEBHOOK_URL:
        _post_discord_embeds(embeds)

    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        for cat, lst in grouped:
            if not lst:
                continue
            text = f"🔥 {cat}\n" + "\n".join(
                _item_line(i, it).replace("**", "") for i, it in enumerate(lst, 1)
            )
            try:
                _http_post_json(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    {"chat_id": TELEGRAM_CHAT_ID, "text": text[:4000],
                     "parse_mode": "Markdown", "disable_web_page_preview": True},
                )
                time.sleep(0.5)
            except Exception as err:  # noqa: BLE001
                print(f"[telegram] échec: {err}", file=sys.stderr)


# --------------------------------------------------------------------------- #
# Suivi des tendances dans le temps (snapshot + comparaison run-à-run)
# --------------------------------------------------------------------------- #

# Mots vides ignorés dans l'extraction de mots-clés des titres.
_STOP = set(
    "de la le les un une et ou pour en the and per con des du au aux avec sans "
    "new neuf neuve neufs taille size lot set état etat tres très bon comme "
    "vinted pz gr cm mit für und nur sur dans plus mini maxi style nuovo nuova "
    "vintage".split()
)
# Note : "vintage" est volontairement ignoré comme mot-clé (trop générique, il
# domine tout) — on le suit séparément si besoin.


def _keywords(title: str) -> set[str]:
    """Mots-clés normalisés d'un titre (>=3 lettres, hors mots vides/nombres)."""
    out: set[str] = set()
    for w in re.findall(r"[a-zà-ÿ0-9']{3,}", title.lower()):
        if w in _STOP or w.isdigit():
            continue
        out.add(w)
    return out


def build_snapshot(items: list[dict]) -> dict:
    """Instantané comparable : favoris cumulés par mot-clé / sous-catégorie /
    annonce. Les favoris (pas le simple comptage) reflètent la demande."""
    kw: dict[str, int] = {}
    kw_n: dict[str, int] = {}
    cat: dict[str, int] = {}
    item_fav: dict[str, int] = {}
    for it in items:
        fav = it.get("favourites") or 0
        for w in _keywords(it.get("title") or ""):
            kw[w] = kw.get(w, 0) + fav
            kw_n[w] = kw_n.get(w, 0) + 1
        c = it.get("category") or "?"
        cat[c] = cat.get(c, 0) + fav
        if it.get("id") is not None:
            item_fav[str(it["id"])] = fav
    # On ne garde que les mots-clés vus dans >=3 annonces (réduit le bruit).
    kw = {w: v for w, v in kw.items() if kw_n.get(w, 0) >= 3}
    return {
        "ts": int(time.time()),
        "keywords": kw,
        "keyword_counts": {w: kw_n[w] for w in kw},
        "subcats": cat,
        "items": item_fav,
    }


def load_history() -> dict:
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"runs": []}


def save_history(hist: dict) -> None:
    os.makedirs(os.path.dirname(HISTORY_FILE) or ".", exist_ok=True)
    hist["runs"] = hist["runs"][-HISTORY_MAX_RUNS:]
    with open(HISTORY_FILE, "w", encoding="utf-8") as fh:
        json.dump(hist, fh, ensure_ascii=False)


def _rising(cur: dict, prev: dict, min_now: int) -> list[tuple[str, int, int, int]]:
    """Renvoie [(clé, valeur_now, delta, pct)] triés par delta décroissant,
    pour les clés dont la valeur actuelle dépasse min_now."""
    out = []
    for k, now in cur.items():
        if now < min_now:
            continue
        before = prev.get(k, 0)
        delta = now - before
        if delta <= 0:
            continue
        pct = int(delta * 100 / before) if before else 999
        out.append((k, now, delta, pct))
    out.sort(key=lambda x: x[2], reverse=True)
    return out


def compute_trends(cur: dict, prev: dict) -> dict:
    """Compare deux instantanés et renvoie ce qui monte."""
    dt_h = max((cur["ts"] - prev["ts"]) / 3600.0, 0.1)
    new_kw = [
        (k, v)
        for k, v in sorted(cur["keywords"].items(), key=lambda x: x[1], reverse=True)
        if k not in prev.get("keywords", {}) and v >= 40
    ]
    return {
        "dt_hours": dt_h,
        "keywords": _rising(cur["keywords"], prev.get("keywords", {}), min_now=40),
        "subcats": _rising(cur["subcats"], prev.get("subcats", {}), min_now=80),
        "new_keywords": new_kw,
    }


def item_gainers(items: list[dict], prev: dict) -> list[dict]:
    """Annonces présentes au run précédent qui ont gagné le plus de favoris."""
    prev_items = prev.get("items", {})
    out = []
    for it in items:
        iid = str(it.get("id"))
        if iid in prev_items:
            delta = (it.get("favourites") or 0) - prev_items[iid]
            if delta > 0:
                g = dict(it)
                g["delta_fav"] = delta
                out.append(g)
    out.sort(key=lambda x: x["delta_fav"], reverse=True)
    return out


def print_trends(trends: dict, gainers: list[dict]) -> None:
    dt = trends["dt_hours"]
    since = f"{dt:.0f}h" if dt < 48 else f"{dt/24:.1f}j"
    print("\n" + "=" * 92)
    print(f"📈 TENDANCES QUI MONTENT (vs run précédent, il y a {since})")
    print("=" * 92)
    print("\n• Mots-clés en hausse (favoris cumulés) :")
    for k, now, delta, pct in trends["keywords"][:TOP_TRENDS]:
        print(f"   +{delta:<5} (+{pct}%)  {k}  → {now} favoris")
    if trends["new_keywords"]:
        print("\n• Mots-clés ÉMERGENTS (absents au run précédent) :")
        for k, v in trends["new_keywords"][:TOP_TRENDS]:
            print(f"   ✦ {k}  ({v} favoris)")
    print("\n• Sous-catégories en hausse :")
    for k, now, delta, pct in trends["subcats"][:8]:
        print(f"   +{delta:<6} (+{pct}%)  {k}")
    if gainers:
        print("\n• Annonces qui décollent (favoris gagnés depuis le dernier run) :")
        for it in gainers[:TOP_TRENDS]:
            print(
                f"   +{it['delta_fav']:<4} ❤{it['favourites']:<4} "
                f"{_euro(it['price']):>8}  {it['title'][:44]:<44}  {it['url']}"
            )
    print("\n" + "=" * 92 + "\n")


def send_trends_digest(trends: dict, gainers: list[dict]) -> None:
    """Embed Discord/Telegram dédié aux tendances montantes."""
    dt = trends["dt_hours"]
    since = f"{dt:.0f}h" if dt < 48 else f"{dt/24:.1f}j"
    parts = []
    if trends["keywords"]:
        parts.append("**🔑 Mots-clés en hausse**\n" + "\n".join(
            f"`+{d}` (+{p}%) **{k}** → {n}❤"
            for k, n, d, p in trends["keywords"][:TOP_TRENDS]
        ))
    if trends["new_keywords"]:
        parts.append("**✦ Émergents**\n" + ", ".join(
            f"{k} ({v}❤)" for k, v in trends["new_keywords"][:TOP_TRENDS]
        ))
    if gainers:
        parts.append("**🚀 Annonces qui décollent**\n" + "\n".join(
            f"`+{it['delta_fav']}❤` {_euro(it['price'])} — [{it['title'][:38]}]({it['url']})"
            for it in gainers[:8]
        ))
    if not parts:
        return
    body = "\n\n".join(parts)
    title = f"📈 Tendances Vinted qui montent (depuis {since})"
    if DRY_RUN:
        print("[DRY_RUN] digest tendances:\n" + title + "\n" + body[:1500])
        return
    if DISCORD_WEBHOOK_URL:
        try:
            _http_post_json(DISCORD_WEBHOOK_URL, {"embeds": [
                {"title": title, "description": body[:4000], "color": 0xF1C40F,
                 "footer": {"text": "Vinted — détection de tendances"}}
            ]})
        except Exception as err:  # noqa: BLE001
            print(f"[discord] échec tendances: {err}", file=sys.stderr)
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            _http_post_json(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                {"chat_id": TELEGRAM_CHAT_ID, "text": (title + "\n\n" + body).replace("**", "")[:4000],
                 "parse_mode": "Markdown", "disable_web_page_preview": True},
            )
        except Exception as err:  # noqa: BLE001
            print(f"[telegram] échec tendances: {err}", file=sys.stderr)


def track_trends(items: list[dict]) -> None:
    """Construit l'instantané, le compare au précédent, publie les tendances,
    puis l'enregistre dans l'historique."""
    snap = build_snapshot(items)
    hist = load_history()
    runs = hist.get("runs", [])
    if runs:
        prev = runs[-1]
        trends = compute_trends(snap, prev)
        gainers = item_gainers(items, prev)
        print_trends(trends, gainers)
        send_trends_digest(trends, gainers)
    else:
        print(
            "\n[tendances] Première mesure enregistrée — la comparaison "
            "s'affichera au prochain run.\n"
        )
    runs.append(snap)
    hist["runs"] = runs
    save_history(hist)


# --------------------------------------------------------------------------- #
# Mode "brands" : classement des MARQUES les plus demandées d'une catégorie
# --------------------------------------------------------------------------- #

def scan_brands_in(cat: dict) -> dict:
    """Agrège les marques d'une catégorie : {marque: [n_annonces, favoris]}.
    Boucle sur plusieurs tris pour dépasser le plafond ~960 résultats."""
    agg: dict[str, list[int]] = {}
    seen: set = set()
    for order in BRAND_ORDERS:
        for page in range(1, CATEGORY_MAX_PAGES + 1):
            params = {
                "catalog_ids": cat["id"], "page": page, "per_page": PER_PAGE,
                "order": order, "currency": CURRENCY,
            }
            url = f"{API_ROOT}/catalog/items?" + urllib.parse.urlencode(params)
            try:
                data = http_get_json(url)
            except Exception:  # noqa: BLE001
                break
            raw = data.get("items") or []
            if not raw:
                break
            for it in raw:
                iid = it.get("id")
                if iid in seen:
                    continue
                seen.add(iid)
                b = (it.get("brand_title") or "").strip()
                if not b or b.lower() in BRAND_NOISE or len(b) < 2:
                    continue
                if BRAND_DAYS_WINDOW > 0:
                    ts = _posted_ts(it)
                    if ts is None or (time.time() - ts) / 86400.0 > BRAND_DAYS_WINDOW:
                        continue  # hors fenêtre de fraîcheur
                row = agg.setdefault(b, [0, 0])
                row[0] += 1
                row[1] += int(it.get("favourite_count") or 0)
    return agg


def run_brands() -> int:
    """Classe les marques les plus demandées des catégories ciblées."""
    now = datetime.now(timezone.utc)
    print(f"== Top marques Vinted == {now.isoformat()} — {VINTED_DOMAIN}")
    try:
        cats = list_scan_categories()
    except Exception as err:  # noqa: BLE001
        print(f"[catégories] impossible de lister: {err}", file=sys.stderr)
        return 1
    if not cats:
        print("Aucune catégorie à scanner.", file=sys.stderr)
        return 1
    fenetre = (f"postées ≤ {BRAND_DAYS_WINDOW:.0f}j"
               if BRAND_DAYS_WINDOW > 0 else "toute l'offre active")
    print(f"{len(cats)} catégorie(s), {fenetre}, tris: {', '.join(BRAND_ORDERS)}")

    # Agrégat global (toutes catégories) : marque -> [n, favoris].
    total: dict[str, list[int]] = {}
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        futs = {pool.submit(scan_brands_in, c): c for c in cats}
        done = 0
        for fut in as_completed(futs):
            c = futs[fut]
            for b, (n, f) in fut.result().items():
                row = total.setdefault(b, [0, 0])
                row[0] += n
                row[1] += f
            done += 1
            print(f"  • [{done}/{len(cats)}] {c['title']}")
            sd_notify("WATCHDOG=1")

    rows = [
        {"brand": b, "listings": n, "favourites": f,
         "fav_per_listing": round(f / n, 1)}
        for b, (n, f) in total.items()
        if n >= BRAND_MIN_LISTINGS
    ]
    if not rows:
        print("Aucune marque trouvée.", file=sys.stderr)
        return 1
    by_demand = sorted(rows, key=lambda r: (-r["favourites"], -r["listings"]))
    by_intensity = sorted(
        [r for r in rows if r["listings"] >= max(BRAND_MIN_LISTINGS, 5)],
        key=lambda r: -r["fav_per_listing"],
    )

    print("\n" + "=" * 80)
    print(f"TOP {TOP_BRANDS} MARQUES PAR DEMANDE (favoris cumulés) — {len(rows)} marques")
    print("=" * 80)
    print(f"{'#':>3}  {'marque':<28}{'annonces':>9}{'favoris':>9}{'fav/ann':>9}")
    for i, r in enumerate(by_demand[:TOP_BRANDS], 1):
        print(f"{i:>3}  {r['brand'][:28]:<28}{r['listings']:>9}"
              f"{r['favourites']:>9}{r['fav_per_listing']:>9.1f}")
    print("\n" + "-" * 80)
    print(f"TOP {TOP_BRANDS} MARQUES PAR DÉSIR (favoris/annonce, min 5 annonces)")
    print("-" * 80)
    for i, r in enumerate(by_intensity[:TOP_BRANDS], 1):
        print(f"{i:>3}  {r['brand'][:28]:<28}{r['listings']:>9}"
              f"{r['favourites']:>9}{r['fav_per_listing']:>9.1f}")
    print("=" * 80 + "\n")

    _write_brands(by_demand)
    _send_brands_digest(by_demand, by_intensity)
    return 0


def _write_brands(rows: list[dict]) -> None:
    try:
        os.makedirs(os.path.dirname(BRANDS_JSON) or ".", exist_ok=True)
        with open(BRANDS_JSON, "w", encoding="utf-8") as fh:
            json.dump(
                {"generated_at": datetime.now(timezone.utc).isoformat(),
                 "domain": VINTED_DOMAIN, "n_brands": len(rows), "brands": rows},
                fh, ensure_ascii=False, indent=2,
            )
        print(f"[marques] JSON écrit -> {BRANDS_JSON}")
    except OSError as err:
        print(f"[marques] JSON échec: {err}", file=sys.stderr)
    try:
        os.makedirs(os.path.dirname(BRANDS_CSV) or ".", exist_ok=True)
        with open(BRANDS_CSV, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["rang", "marque", "annonces", "favoris", "fav_par_annonce"])
            for i, r in enumerate(rows, 1):
                w.writerow([i, r["brand"], r["listings"], r["favourites"],
                            r["fav_per_listing"]])
        print(f"[marques] CSV écrit -> {BRANDS_CSV}")
    except OSError as err:
        print(f"[marques] CSV échec: {err}", file=sys.stderr)


def _send_brands_digest(by_demand: list[dict], by_intensity: list[dict]) -> None:
    top = by_demand[:TOP_BRANDS]
    dem = "\n".join(
        f"**{i}.** {r['brand']} — ❤{r['favourites']} ({r['listings']} ann., "
        f"{r['fav_per_listing']:.0f}/ann)"
        for i, r in enumerate(top[:20], 1)
    )
    inten = "\n".join(
        f"**{i}.** {r['brand']} — {r['fav_per_listing']:.0f} fav/ann (❤{r['favourites']})"
        for i, r in enumerate(by_intensity[:12], 1)
    )
    if DRY_RUN:
        print("[DRY_RUN] digest marques:\n" + dem)
        return
    if DISCORD_WEBHOOK_URL:
        embeds = [
            {"title": "🏷️ Top marques par DEMANDE (favoris)", "description": dem[:4000],
             "color": 0x09B1BA},
            {"title": "🔥 Top marques par DÉSIR (favoris/annonce)",
             "description": inten[:4000], "color": 0xF1C40F,
             "footer": {"text": "Vinted — top marques"}},
        ]
        try:
            _http_post_json(DISCORD_WEBHOOK_URL, {"embeds": embeds})
        except Exception as err:  # noqa: BLE001
            print(f"[discord] échec marques: {err}", file=sys.stderr)
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            _http_post_json(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                {"chat_id": TELEGRAM_CHAT_ID,
                 "text": ("🏷️ Top marques (demande)\n" + dem).replace("**", "")[:4000],
                 "parse_mode": "Markdown", "disable_web_page_preview": True},
            )
        except Exception as err:  # noqa: BLE001
            print(f"[telegram] échec marques: {err}", file=sys.stderr)


# --------------------------------------------------------------------------- #
# Mode "deals" : scanner d'affaires (annonces sous le prix du marché)
# --------------------------------------------------------------------------- #

_DEAL_STOP = set(
    "de la le les un une et ou pour en the and per con des du avec sans new neuf "
    "neuve neufs taille size lot set état etat tres très bon comme pz gr cm mit "
    "und nur sur dans plus mini maxi style nuovo nuova vintage pour avec".split()
)


def _model_tokens(it: dict) -> set[str]:
    """Mots significatifs d'un titre (modèle), hors marque et mots vides.
    Sert à ne comparer que des annonces du MÊME produit."""
    brand_toks = {w for w in re.findall(r"[a-zà-ÿ0-9']+", (it.get("brand") or "").lower())}
    out = set()
    for w in re.findall(r"[a-zà-ÿ0-9']{3,}", (it.get("title") or "").lower()):
        if w in _DEAL_STOP or w in brand_toks or w.isdigit():
            continue
        out.add(w)
    return out


# Accessoires & pièces détachées : à ne comparer QU'entre eux (jamais à
# l'appareil complet). Multilingue (FR/EN/IT/ES/PT/DE), racines + pluriels.
_ACCESSORY_RE = re.compile(
    r"\b(coque|housse|étui|etui|custodia|cover|case|capa|capinha|funda|schutz|"
    r"pochette|protect|verre|vitre|vetro|pellicola|cristal|chargeur|c[âa]ble|"
    r"adaptat|adapter|support|stand|dock|grip|sticker|autocollant|bumper|"
    r"sacoche|cam[ée]ra|batter|akku|[ée]cran|display|schermo|pantalla|tampa|"
    r"scocca|t[ée]l[ée]command|telecomando|joystick)\w*",
    re.IGNORECASE,
)
# Mots composés (collés) fréquents en DE/NL/etc.
_ACCESSORY_SUB = ("hülle", "hulle", "schutzfolie", "verpackung")


def _is_accessory(it: dict) -> bool:
    t = (it.get("title") or "").lower()
    return bool(_ACCESSORY_RE.search(t)) or any(s in t for s in _ACCESSORY_SUB)


def find_deals_in_category(cat: dict) -> list[dict]:
    """Trouve les annonces récentes nettement sous le prix du marché.

    - Distribution de prix : annonces 'relevance' (offre représentative).
    - Candidats : annonces 'newest_first' postées depuis <= DEAL_MAX_AGE_DAYS.
    - Comparaison : même marque + au moins un mot-clé de modèle en commun.
    """
    try:
        pool = _fetch_items({"catalog_ids": cat["id"]}, DEAL_REF_PAGES,
                            category=cat["title"], order="relevance")
        pool += _fetch_items({"catalog_ids": cat["id"]}, DEAL_NEW_PAGES,
                             category=cat["title"], order="newest_first")
    except Exception as err:  # noqa: BLE001
        print(f"[deals] {cat['title']}: {err}", file=sys.stderr)
        return []

    # Dédup + index par marque (prix > 0 uniquement).
    by_id: dict = {}
    for it in pool:
        if it.get("id") is not None and it.get("price"):
            by_id.setdefault(it["id"], it)
    items = list(by_id.values())
    by_brand: dict[str, list[dict]] = {}
    for it in items:
        b = (it.get("brand") or "").strip().lower()
        if b and b not in BRAND_NOISE:
            by_brand.setdefault(b, []).append(it)

    deals = []
    for cand in items:
        if (cand.get("age_days") is None or cand["age_days"] > DEAL_MAX_AGE_DAYS
                or cand["price"] < DEAL_MIN_PRICE):
            continue
        if _DEAL_SKIP_RE.search(cand.get("title") or ""):
            continue  # cassé / pièces / boîte vide -> pas un vrai deal
        b = (cand.get("brand") or "").strip().lower()
        mates = by_brand.get(b)
        if not mates or len(mates) < DEAL_MIN_COMPARABLES + 1:
            continue
        toks = _model_tokens(cand)
        if len(toks) < DEAL_MIN_SHARED_TOKENS:
            continue  # titre trop pauvre pour comparer fiablement
        cand_acc = _is_accessory(cand)
        # Comparables = même marque + même nature (accessoire/appareil) +
        # >= N mots de modèle en commun (produit identique).
        comps = [
            m for m in mates
            if m["id"] != cand["id"]
            and _is_accessory(m) == cand_acc
            and len(_model_tokens(m) & toks) >= DEAL_MIN_SHARED_TOKENS
        ]
        if len(comps) < DEAL_MIN_COMPARABLES:
            continue
        prices = sorted(m["price"] for m in comps)
        med = statistics.median(prices)
        if med <= 0 or cand["price"] > med * (1 - DEAL_THRESHOLD):
            continue
        cand = dict(cand)
        cand["market"] = round(med, 2)
        cand["discount"] = round(1 - cand["price"] / med, 2)
        cand["n_comps"] = len(comps)
        deals.append(cand)
    return deals


def _load_deal_state() -> dict:
    try:
        with open(DEAL_STATE_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"seen": {}}


def _save_deal_state(st: dict) -> None:
    os.makedirs(os.path.dirname(DEAL_STATE_FILE) or ".", exist_ok=True)
    # borne la taille (garde les 5000 derniers vus)
    seen = st.get("seen", {})
    if len(seen) > 5000:
        st["seen"] = dict(list(seen.items())[-5000:])
    with open(DEAL_STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(st, fh, ensure_ascii=False)


def _send_deal(v: dict) -> None:
    pct = int(v["discount"] * 100)
    title = v["title"][:200]
    desc = (
        f"**{_euro(v['price'])}**  ~~{_euro(v['market'])}~~   •   **-{pct}%** "
        f"sous le marché\n"
        f"🏷 {v.get('brand') or '—'} · 📦 {v.get('status') or '—'} · "
        f"⏱ {_age_label(v.get('age_days'))} · 📊 {v['n_comps']} comparables"
    )
    if DRY_RUN:
        print(f"[DRY_RUN][deal -{pct}%] {_euro(v['price'])} (marché {_euro(v['market'])}) "
              f"{title} — {v['url']}")
        return
    if DISCORD_WEBHOOK_URL:
        embed = {
            "title": f"💸 -{pct}% · {title}"[:256],
            "url": v["url"],
            "description": desc,
            "color": 0xE74C3C if pct >= 50 else 0xE67E22,
            "footer": {"text": "Vinted — affaire détectée"},
        }
        if v.get("image"):
            embed["thumbnail"] = {"url": v["image"]}
        try:
            _http_post_json(DISCORD_WEBHOOK_URL, {"embeds": [embed]})
        except Exception as err:  # noqa: BLE001
            print(f"[discord] échec deal: {err}", file=sys.stderr)
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        txt = (f"💸 -{pct}% sous le marché\n{title}\n{_euro(v['price'])} "
               f"(marché {_euro(v['market'])})\n{v['url']}")
        try:
            _http_post_json(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                {"chat_id": TELEGRAM_CHAT_ID, "text": txt, "disable_web_page_preview": False},
            )
        except Exception as err:  # noqa: BLE001
            print(f"[telegram] échec deal: {err}", file=sys.stderr)


def run_deals() -> int:
    """Scanne les catégories et alerte sur les annonces sous le prix du marché."""
    now = datetime.now(timezone.utc)
    print(f"== Scanner d'affaires Vinted == {now.isoformat()} — {VINTED_DOMAIN}")
    try:
        cats = list_scan_categories()
    except Exception as err:  # noqa: BLE001
        print(f"[deals] catégories: {err}", file=sys.stderr)
        return 1
    if not cats:
        print("Aucune catégorie à scanner.", file=sys.stderr)
        return 1
    print(f"{len(cats)} catégorie(s) · seuil -{int(DEAL_THRESHOLD*100)}% · "
          f"candidats postés ≤ {DEAL_MAX_AGE_DAYS:.0f}j")

    state = _load_deal_state()
    seen = state.setdefault("seen", {})
    found = 0
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        futs = {pool.submit(find_deals_in_category, c): c for c in cats}
        done = 0
        for fut in as_completed(futs):
            for v in fut.result():
                key = str(v["id"])
                prev = seen.get(key)
                # alerte si jamais vu, ou si le prix a encore baissé
                if prev is not None and v["price"] >= float(prev):
                    continue
                _send_deal(v)
                seen[key] = v["price"]
                found += 1
            done += 1
            if done % 10 == 0:
                print(f"  • {done}/{len(cats)} catégories scannées, {found} affaire(s)")
            sd_notify("WATCHDOG=1")

    _save_deal_state(state)
    print(f"Terminé : {found} affaire(s) détectée(s).")
    return 0


def run_categories() -> int:
    """Scanne toutes les catégories (hors vêtements) et liste les produits
    récents les plus likés."""
    now = datetime.now(timezone.utc)
    print(f"== Scan catégories Vinted == {now.isoformat()} — {VINTED_DOMAIN}")
    try:
        cats = list_scan_categories()
    except Exception as err:  # noqa: BLE001
        print(f"[catégories] impossible de lister: {err}", file=sys.stderr)
        return 1
    if not cats:
        print("Aucune catégorie à scanner.", file=sys.stderr)
        return 1
    print(
        f"{len(cats)} catégories (hors: {', '.join(EXCLUDE_PATTERNS)}), "
        f"fenêtre {DAYS_WINDOW:.0f}j, {CATEGORY_MAX_PAGES} page(s)/cat."
    )

    all_items: dict = {}  # id -> item (dédup inter-catégories)
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        futs = {pool.submit(scan_category, c): c for c in cats}
        done = 0
        for fut in as_completed(futs):
            c = futs[fut]
            got = fut.result()
            for it in got:
                if it["favourites"] < MIN_FAVOURITES:
                    continue
                prev = all_items.get(it["id"])
                if prev is None or it["favourites"] > prev["favourites"]:
                    all_items[it["id"]] = it
            done += 1
            print(f"  • [{done}/{len(cats)}] {c['title']}: {len(got)} récents")
            sd_notify("WATCHDOG=1")

    items = list(all_items.values())
    if not items:
        print("Aucun article récent trouvé.", file=sys.stderr)
        return 1
    print(f"\n{len(items)} articles récents (≥{MIN_FAVOURITES} favoris) collectés.")

    grouped = group_by_category(items)  # par catégorie › sous-catégorie
    print_products_report(grouped, cats)
    write_products_report(grouped)
    send_products_digest(grouped)
    if TRACK_TRENDS:
        track_trends(items)
    return 0


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def run_watchlist() -> int:
    queries = load_queries()
    if not queries:
        print(
            "Aucune recherche à analyser. Renseigne VINTED_QUERIES ou "
            f"crée {WATCHLIST_FILE} (une recherche par ligne).",
            file=sys.stderr,
        )
        return 1

    now = datetime.now(timezone.utc)
    if FETCH_VIEWS and not (VINTED_COOKIE or VINTED_ACCESS_TOKEN):
        print(
            "[info] FETCH_VIEWS=true mais aucune session (VINTED_COOKIE) : les "
            "vues ne sont pas accessibles en anonyme, classement sur les FAVORIS.",
            file=sys.stderr,
        )
    views_mode = (
        "oui" if FETCH_VIEWS and (VINTED_COOKIE or VINTED_ACCESS_TOKEN) else "non"
    )
    print(f"== Analyseur demande Vinted == {now.isoformat()} — {VINTED_DOMAIN}")
    print(f"{len(queries)} recherche(s), {MAX_PAGES} page(s)/recherche, "
          f"vues={views_mode}")

    results: list[dict] = []
    for q in queries:
        print(f"  • analyse: {q}")
        r = analyze_query(q)
        if r:
            results.append(r)
        sd_notify("WATCHDOG=1")

    if not results:
        print("Aucun résultat exploitable.", file=sys.stderr)
        return 1

    print_checks(results)
    write_reports(results)
    send_checks(results)
    return 0


def _check_lines(r: dict) -> list[str]:
    v = r["verdict"]
    vel = (f"≈ {r['velocity']:.0f} annonces/jour postées (rythme de vente)"
           if r.get("velocity") else "rythme de vente indéterminé")
    lines = [
        f"{v['emoji']} **{r['query'].upper()}** — {v['label']}  ·  score revente "
        f"**{r.get('score', 0)}/100**",
        f"📦 Offre : **{_fmt_total(r['n_total'])}** annonces ({v['note']})",
        f"🔁 Écoulement : **{vel}**",
        f"❤️ Demande : **{(r['avg_favourites'] or 0):.1f} favoris/annonce** · "
        f"max {r['max_favourites']} · {r.get('pct_hot', 0)}% des annonces ont >10 likes",
        f"💰 Achat malin **{_euro(r.get('p25_price'))}** → revente **{_euro(r['median_price'])}** "
        f"→ haut {_euro(r.get('p75_price'))}  (marge ~**{_euro(r.get('margin'))}**)",
        f"🎯 Pertinence : **{r.get('match_pct', 0)}%** des annonces contiennent bien tes mots-clés "
        f"({r.get('n_listings', 0)}/{r.get('n_scanned', 0)} analysées)",
    ]
    conds = r.get("conditions") or {}
    if conds:
        top = list(conds.items())[:3]
        lines.append(
            "📦 États : " + " · ".join(
                f"{c} ({d['n']}, méd. {_euro(d['median'])})" for c, d in top
            )
        )
    return lines


def print_checks(results: list[dict]) -> None:
    print("\n" + "=" * 70)
    print("VÉRIFICATION REVENTE VINTED")
    print("=" * 70)
    for r in results:
        print("\n" + "\n".join(l.replace("**", "") for l in _check_lines(r)))
    print("\n" + "=" * 70 + "\n")


def send_checks(results: list[dict]) -> None:
    """Un embed Discord par mot-clé, avec le verdict revente."""
    if DRY_RUN:
        for r in results:
            print("[DRY_RUN] " + " | ".join(l.replace("**", "") for l in _check_lines(r)))
        return
    if DISCORD_WEBHOOK_URL:
        embeds = []
        for r in results:
            v = r["verdict"]
            color = (0x2ECC71 if v["emoji"] == "🟢"
                     else 0xF1C40F if v["emoji"] == "🟡" else 0xE74C3C)
            embeds.append({
                "title": f"{v['emoji']} {r['query']} — {v['label']}"[:256],
                "description": "\n".join(_check_lines(r)[1:])[:4000],
                "color": color,
                "footer": {"text": "Vinted — vérif revente"},
            })
        try:
            _post_discord_embeds(embeds)
        except Exception as err:  # noqa: BLE001
            print(f"[discord] échec check: {err}", file=sys.stderr)
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        for r in results:
            try:
                _http_post_json(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    {"chat_id": TELEGRAM_CHAT_ID,
                     "text": "\n".join(_check_lines(r)).replace("**", ""),
                     "parse_mode": "Markdown", "disable_web_page_preview": True},
                )
            except Exception as err:  # noqa: BLE001
                print(f"[telegram] échec check: {err}", file=sys.stderr)


def run_once() -> int:
    """Choisit le mode : scan catégories (défaut), watchlist ou brands."""
    # Diagnostic config : indique si une notif est branchée (sans révéler l'URL).
    notif = []
    if DISCORD_WEBHOOK_URL:
        notif.append(f"Discord ✅ (…{DISCORD_WEBHOOK_URL[-6:]})")
    else:
        notif.append("Discord ❌ ABSENT (secret VINTED_WEBHOOK/DISCORD_WEBHOOK_URL vide)")
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        notif.append("Telegram ✅")
    print(f"[config] MODE={MODE} · notifications: {' · '.join(notif)}")
    if MODE == "watchlist":
        return run_watchlist()
    if MODE == "brands":
        return run_brands()
    if MODE == "deals":
        return run_deals()
    return run_categories()


def main() -> int:
    if not LOOP_ENABLED:
        return run_once()

    deadline = (time.monotonic() + LOOP_MAX_SECONDS) if LOOP_MAX_SECONDS > 0 else None
    duree = "illimité" if deadline is None else f"~{LOOP_MAX_SECONDS // 60} min"
    print(
        f"Mode BOUCLE : analyse toutes les ~{LOOP_INTERVAL_SECONDS}s, durée {duree}."
    )
    sd_notify("READY=1")
    while True:
        try:
            run_once()
        except Exception as err:  # noqa: BLE001
            print(f"[boucle] erreur: {err}", file=sys.stderr)
        sd_notify("WATCHDOG=1")
        if deadline is not None and time.monotonic() >= deadline:
            print("Fin de la fenêtre de boucle.")
            return 0
        time.sleep(LOOP_INTERVAL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
