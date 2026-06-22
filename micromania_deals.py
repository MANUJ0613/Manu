#!/usr/bin/env python3
"""
Détecteur de bons plans / erreurs de prix Micromania.

Principe :
  1. Lit l'index des sitemaps Micromania pour récupérer les sitemaps "product".
  2. Ne garde que les fiches produits modifiées (lastmod) depuis le dernier run,
     ce qui rend chaque exécution légère.
  3. Pour chaque fiche, extrait le prix actuel et le prix de référence (barré)
     à partir du bloc analytics embarqué dans la page.
  4. Déclenche une alerte quand un produit NEUF a :
        - un prix de référence  >= MIN_REFERENCE_PRICE (par défaut 50 €)
        - une réduction         >= DISCOUNT_THRESHOLD   (par défaut 50 %)
  5. Envoie l'alerte vers Discord et/ou Telegram (ou l'écrit dans un fichier).
  6. Mémorise les deals déjà signalés (state) pour ne pas spammer.

Aucune dépendance externe : uniquement la bibliothèque standard Python 3.

Configuration via variables d'environnement (voir README.md).
"""

from __future__ import annotations

import gzip
import html
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

# --------------------------------------------------------------------------- #
# Configuration (surchargée par l'environnement)
# --------------------------------------------------------------------------- #

SITEMAP_INDEX = os.environ.get(
    "SITEMAP_INDEX", "https://www.micromania.fr/sitemap_index.xml"
)
USER_AGENT = os.environ.get(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
)

DISCOUNT_THRESHOLD = float(os.environ.get("DISCOUNT_THRESHOLD", "0.50"))  # 50 %
# Deals jusqu'aux accessoires à ~5 € de référence -> seuil très bas.
MIN_REFERENCE_PRICE = float(os.environ.get("MIN_REFERENCE_PRICE", "3"))  # €
INITIAL_WINDOW_HOURS = float(os.environ.get("INITIAL_WINDOW_HOURS", "24"))
MAX_PRODUCTS = int(os.environ.get("MAX_PRODUCTS", "20000"))  # couvre tout le catalogue

# Certains produits (notamment les PACKS) ont une fiche mais ne sont PAS dans
# le sitemap (URL en /...-mbNNN.html). On scanne donc aussi ces pages catégorie
# pour en extraire les URLs produits manquantes. Toujours scannées (pas de
# lastmod). Liste de slugs /c/<slug> séparés par des virgules.
def _csv_env(name: str, default: str) -> list[str]:
    return [c.strip() for c in os.environ.get(name, default).split(",") if c.strip()]


# Sur le VPS, DataDome sert des fiches produits "allégées" (sans prix). En
# revanche les pages CATÉGORIE contiennent les prix dans les tuiles (data-gtm).
# On détecte donc les prix depuis les catégories, pas depuis chaque fiche.
#
# Catégories "rapides" (petites) scannées à CHAQUE passage : packs, collectors,
# exclusivités, premium.
FAST_CATEGORIES = _csv_env(
    "FAST_CATEGORIES",
    "tous-nos-packs,jeux-video-edition-collector,"
    "exclusivites-micromania,exclusivites-premium,produits-derives-premium",
)
# Catégories "complètes" (grosses) scannées au passage COMPLET : tous les
# supports de jeux + figurines (goodies).
FULL_CATEGORIES = _csv_env(
    "FULL_CATEGORIES",
    "jeux-ps5,jeux-xbox,jeux-switch,jeux-ps4,jeux-pc,"
    "figurines,tous-les-produits-derives,mugs-et-verres,peluches,sacs",
)
# sz modéré + pagination (les pages à fort sz dépassent 20 Mo et timeout).
CATEGORY_SZ = int(os.environ.get("CATEGORY_SZ", "120"))
CATEGORY_MAX_PAGES = int(os.environ.get("CATEGORY_MAX_PAGES", "20"))
# IMPORTANT : trop de requêtes catégorie en parallèle => curl_cffi/DataDome
# renvoie des pages vides. On limite donc fortement la concurrence catégorie.
CATEGORY_CONCURRENCY = int(os.environ.get("CATEGORY_CONCURRENCY", "3"))
# Scanner les fiches /p/ du sitemap via parse_product (avec fallback prix
# visible). Couvre TOUT le catalogue listé, y compris les deals retrait/rupture
# cachés des listings catégorie. Lourd (~13k pages) mais complet.
SCAN_SITEMAP = os.environ.get("SCAN_SITEMAP", "false").lower() == "true"

# Énumération des packs par ID : /mbN.html redirige vers la fiche du pack
# (même pour des packs éphémères jamais listés dans une catégorie). On sonde
# toute la plage mb1..mbMAX pour ne rater aucun pack flash / erreur de prix.
# Désactivé par défaut : 740 requêtes /mbN.html d'un coup font challenger
# l'anti-bot (et risquent de flaguer l'IP). Les packs listés sont déjà couverts
# par la catégorie tous-nos-packs. À n'activer qu'avec un proxy costaud.
PACK_ID_ENUM = os.environ.get("PACK_ID_ENUM", "false").lower() == "true"
PACK_ID_MAX = int(os.environ.get("PACK_ID_MAX", "0"))  # 0 = auto (max connu + buffer)
PACK_ID_BUFFER = int(os.environ.get("PACK_ID_BUFFER", "40"))
CONCURRENCY = int(os.environ.get("CONCURRENCY", "8"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "20"))
INCLUDE_USED = os.environ.get("INCLUDE_USED", "false").lower() == "true"
INCLUDE_PRECOMMANDE = os.environ.get("INCLUDE_PRECOMMANDE", "false").lower() == "true"
# On alerte sur TOUT vrai drop de prix, même rupture web / retrait magasin
# (beaucoup de bons deals Dealabs sont en "retrait magasin"). La dispo est
# indiquée dans l'alerte. Mettre "false" pour ne garder que le dispo web.
INCLUDE_UNAVAILABLE = os.environ.get("INCLUDE_UNAVAILABLE", "true").lower() == "true"

# Mode boucle : si LOOP_INTERVAL_SECONDS >= 0 et LOOP_MODE actif, le script
# reste actif et relance un scan en continu, pendant au plus LOOP_MAX_SECONDS.
# LOOP_INTERVAL_SECONDS = pause entre deux scans complets.
LOOP_ENABLED = os.environ.get("LOOP_ENABLED", "false").lower() == "true"
LOOP_INTERVAL_SECONDS = int(os.environ.get("LOOP_INTERVAL_SECONDS", "60"))
# Durée max d'un run en boucle. 0 = illimité (idéal sur un VPS/systemd).
LOOP_MAX_SECONDS = int(os.environ.get("LOOP_MAX_SECONDS", "19800"))  # ~5h30
# Boucle à deux vitesses :
#  - passage RAPIDE (chaque itération) : seulement les sources haute priorité
#    (packs + collectors/exclus + énumération d'IDs de packs) — léger, ~1-2 min.
#  - passage COMPLET (catalogue entier) : seulement toutes les
#    FULL_CATALOG_EVERY_MINUTES, car lourd (~6-7 min) et le lastmod n'est pas
#    fiable. Mettre 0 pour faire un scan complet à chaque passage.
FULL_CATALOG_EVERY_MINUTES = float(os.environ.get("FULL_CATALOG_EVERY_MINUTES", "30"))

STATE_FILE = os.environ.get("STATE_FILE", "state/state.json")
DEALS_LOG = os.environ.get("DEALS_LOG", "state/deals.log")
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
FULL_SCAN = os.environ.get("FULL_SCAN", "false").lower() == "true"

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
# Routage multi-salons : un webhook par catégorie et/ou un salon "pépites"
# (deals chers). Non renseigné => retombe sur DISCORD_WEBHOOK_URL.
WEBHOOK_JEUX = os.environ.get("DISCORD_WEBHOOK_JEUX", "").strip()
WEBHOOK_COLLECTOR = os.environ.get("DISCORD_WEBHOOK_COLLECTOR", "").strip()
WEBHOOK_GOODIES = os.environ.get("DISCORD_WEBHOOK_GOODIES", "").strip()
WEBHOOK_PEPITES = os.environ.get("DISCORD_WEBHOOK_PEPITES", "").strip()
# Prix de référence à partir duquel un deal va dans le salon "pépites".
PEPITE_MIN = float(os.environ.get("PEPITE_MIN", "80"))

def _slug_type(slug: str) -> str:
    """Type de deal (jeux / collector / goodies) d'après le slug catégorie."""
    s = slug.lower()
    # Collector/packs/exclus d'abord (avant le préfixe générique "jeux-").
    if (
        "collector" in s
        or "edition-limitee" in s
        or s.startswith("exclusivites")
        or s.startswith("tous-nos-packs")
        or s == "produits-derives-premium"
    ):
        return "collector"
    if s.startswith("jeux-") or s == "retrogaming":
        return "jeux"
    return "goodies"


def _webhook_for(v: dict) -> str:
    """Choisit le salon Discord selon le prix (pépites) puis la catégorie."""
    if WEBHOOK_PEPITES and v.get("reference", 0) >= PEPITE_MIN:
        return WEBHOOK_PEPITES
    wh = {
        "jeux": WEBHOOK_JEUX,
        "collector": WEBHOOK_COLLECTOR,
        "goodies": WEBHOOK_GOODIES,
    }.get(v.get("type", ""), "")
    return wh or DISCORD_WEBHOOK_URL


ANY_DISCORD = bool(
    DISCORD_WEBHOOK_URL
    or WEBHOOK_JEUX
    or WEBHOOK_COLLECTOR
    or WEBHOOK_GOODIES
    or WEBHOOK_PEPITES
)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()


# --------------------------------------------------------------------------- #
# Watchdog systemd : heartbeat via sd_notify (sans dépendance). No-op si le
# script ne tourne pas sous systemd (NOTIFY_SOCKET absent).
# --------------------------------------------------------------------------- #

def sd_notify(state: str) -> None:
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    try:
        if addr.startswith("@"):  # socket abstrait
            addr = "\0" + addr[1:]
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        s.connect(addr)
        s.sendall(state.encode())
        s.close()
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# HTTP — Micromania est protégé par DataDome. Depuis une IP datacenter (VPS),
# les requêtes "nues" (urllib) sont bloquées en 403. Il faut :
#   1) imiter l'empreinte TLS d'un vrai Chrome  -> curl_cffi(impersonate="chrome")
#   2) faire un GET "à froid" de la home pour obtenir le cookie DataDome, puis
#      le réutiliser sur les requêtes suivantes.
# Si curl_cffi n'est pas installé, on retombe sur urllib (OK sur une IP non
# bloquée, ex. GitHub Actions / dev).
# --------------------------------------------------------------------------- #

IMPERSONATE = os.environ.get("IMPERSONATE", "chrome")

# Proxy (idéalement RÉSIDENTIEL "sticky") pour contourner le bannissement
# Incapsula des IP datacenter. Ex : http://user:pass@host:port
# Avec un proxy résidentiel, on peut remonter la cadence sans se faire bannir.
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
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9",
}

# Incapsula/Imperva exige une SESSION cohérente (mêmes connexion/cookies/TLS).
# On garde donc une Session curl_cffi par thread, réchauffée par un GET "à
# froid" de la home — sinon on reçoit une page-challenge de ~200 octets.
_tls = threading.local()
_warm_logged = [False]
_warm_lock = threading.Lock()


def _is_challenge(content: bytes) -> bool:
    """Détecte la page-challenge anti-bot (Incapsula/DataDome)."""
    if len(content) < 600:
        return True
    head = content[:3000].lower()
    return (
        b"_incapsula_resource" in head
        or b"incident_id" in head
        or b"/_incapsula" in head
    )


def _new_session():
    s = cffi.Session(impersonate=IMPERSONATE, proxies=_PROXIES)
    try:
        s.headers.update(_BROWSER_HEADERS)
    except Exception:  # noqa: BLE001
        pass
    # Réchauffe : GET home à froid DANS cette session (pose les cookies anti-bot).
    try:
        s.get(SITE_ROOT + "/", timeout=REQUEST_TIMEOUT)
    except Exception:  # noqa: BLE001
        pass
    with _warm_lock:
        if not _warm_logged[0]:
            _warm_logged[0] = True
            print("[warmup] session curl_cffi réchauffée (home à froid)")
    return s


def _session():
    s = getattr(_tls, "s", None)
    if s is None:
        s = _new_session()
        _tls.s = s
    return s


def http_get(url: str, retries: int = 3) -> bytes:
    """GET résistant à Incapsula/DataDome (session curl_cffi réchauffée)."""
    last_err: Exception | None = None

    if HAVE_CFFI:
        for attempt in range(retries):
            try:
                r = _session().get(url, timeout=REQUEST_TIMEOUT)
                if r.status_code in (404, 410):
                    raise RuntimeError(f"GET {url}: HTTP {r.status_code}")
                if r.status_code == 403 or _is_challenge(r.content):
                    # Bloqué : on jette la session et on en réchauffe une neuve.
                    _tls.s = None
                    last_err = RuntimeError("blocage anti-bot (challenge)")
                    time.sleep(1.0 * (attempt + 1))
                    continue
                if r.status_code >= 400:
                    last_err = RuntimeError(f"HTTP {r.status_code}")
                    time.sleep(1.0 * (attempt + 1))
                    continue
                return r.content
            except RuntimeError:
                raise
            except Exception as err:  # noqa: BLE001
                _tls.s = None  # la session peut être cassée
                last_err = err
                time.sleep(1.0 * (attempt + 1))
        raise RuntimeError(f"GET échoué pour {url}: {last_err}")

    # --- Repli urllib (IP non bloquée) ---
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={**_BROWSER_HEADERS, "Accept-Encoding": "gzip"},
            )
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw
        except urllib.error.HTTPError as err:
            if err.code in (404, 410, 403):
                raise RuntimeError(f"GET {url}: HTTP {err.code}") from err
            last_err = err
            time.sleep(1.5 * (attempt + 1))
        except Exception as err:  # noqa: BLE001
            last_err = err
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET échoué pour {url}: {last_err}")


# --------------------------------------------------------------------------- #
# Sitemaps
# --------------------------------------------------------------------------- #

LOC_RE = re.compile(r"<loc>\s*([^<]+?)\s*</loc>", re.IGNORECASE)
URL_BLOCK_RE = re.compile(r"<url>(.*?)</url>", re.IGNORECASE | re.DOTALL)
LASTMOD_RE = re.compile(r"<lastmod>\s*([^<]+?)\s*</lastmod>", re.IGNORECASE)


def get_product_sitemaps() -> list[str]:
    """Retourne la liste des sitemaps 'product' depuis l'index."""
    xml = http_get(SITEMAP_INDEX).decode("utf-8", "replace")
    locs = LOC_RE.findall(xml)
    product = [u for u in locs if "product" in u.lower()]
    return product or locs


SITE_ROOT = "https://www.micromania.fr"
# Liens d'une page catégorie. On ne garde que les fiches produits :
#   - fiches classiques  /p/....html
#   - packs              /....-mbNNN.html  (souvent hors sitemap)
HREF_RE = re.compile(
    r'href=["\'](?:https?://www\.micromania\.fr)?(/[^"\'#?]+\.html)["\']'
)
PACK_SUFFIX_RE = re.compile(r"-mb\d+\.html$")
MB_URL_RE = re.compile(r"/mb(\d+)\.html")


NAME_RE = re.compile(r'"name":"([^"]*)"')
GID_RE = re.compile(r'"id":"(\d+)"')
TOTAL_RE = re.compile(r"(\d+)\s*produits", re.IGNORECASE)


def _extract_tiles(page: str, out: dict) -> None:
    """Ajoute à `out` (clé = id produit) les tuiles priced d'une page."""
    page = html.unescape(page)
    urls: dict[str, str] = {}
    for u in set(re.findall(r"/p/[a-z0-9\-]+\.html", page)):
        m = re.search(r"-(\d+)\.html$", u)
        if m:
            urls[m.group(1)] = SITE_ROOT + u
    for mt in METRIC_RE.finditer(page):
        obj = _enclosing_object(page, mt.start())
        gid = GID_RE.search(obj)
        if not gid:
            continue
        pid = gid.group(1)
        if pid in out or pid not in urls:
            continue
        dispo_m = DISPO_RE.search(obj)
        name_m = NAME_RE.search(obj)
        cond_m = COND_RE.search(obj)
        out[pid] = {
            "url": urls[pid],
            "title": name_m.group(1) if name_m else "",
            "condition": cond_m.group(1) if cond_m else "new",
            "current": float(mt.group("cur")),
            "reference": float(mt.group("ref")),
            "precommande": int(pr.group(1)) if (pr := PRECO_RE.search(obj)) else 0,
            "edition": e.group(1) if (e := EDITION_RE.search(obj)) else "",
            "platform": p.group(1) if (p := PLATFORM_RE.search(obj)) else "",
            "image": i.group(1) if (i := IMAGE_RE.search(obj)) else "",
            "pegi": pe.group(1) if (pe := PEGI_RE.search(obj)) else "",
            "genre": g.group(1) if (g := GENRE_RE.search(obj)) else "",
            "available": (int(dispo_m.group(1)) == 1) if dispo_m else True,
        }


def parse_category_tiles(slug: str) -> list[dict]:
    """Lit produits + PRIX depuis une page catégorie, avec pagination.

    Les tuiles embarquent un bloc analytics (data-gtm) avec name, metric1
    (prix actuel), metric2 (prix de référence), condition, dispoweb, image…
    joint à l'URL produit via l'id. Méthode fiable sur le VPS (les fiches /p/
    y sont servies sans prix par DataDome) et couvre aussi les goodies qui ne
    sont PAS dans le sitemap.
    """
    base = slug if slug.startswith("http") else f"{SITE_ROOT}/c/{slug}"
    out: dict[str, dict] = {}
    total: int | None = None
    start = 0
    for _ in range(CATEGORY_MAX_PAGES):
        sep = "&" if "?" in base else "?"
        page = http_get(f"{base}{sep}sz={CATEGORY_SZ}&start={start}").decode(
            "utf-8", "replace"
        )
        if total is None:
            tm = TOTAL_RE.search(html.unescape(page))
            total = int(tm.group(1)) if tm else CATEGORY_SZ
        before = len(out)
        _extract_tiles(page, out)
        start += CATEGORY_SZ
        if start >= total or len(out) == before:
            break
    return list(out.values())


def get_category_products(slug: str) -> set[str]:
    """Extrait les URLs produits d'une page catégorie (packs inclus)."""
    url = slug if slug.startswith("http") else f"{SITE_ROOT}/c/{slug}?sz={CATEGORY_SZ}"
    page = http_get(url).decode("utf-8", "replace")
    found = set()
    for m in HREF_RE.finditer(page):
        href = m.group(1)
        if href.startswith("/p/") or PACK_SUFFIX_RE.search(href):
            found.add(SITE_ROOT + href)
    return found


def parse_sitemap(url: str) -> list[tuple[str, datetime | None]]:
    """Retourne [(url_produit, lastmod)] pour un sitemap product."""
    xml = http_get(url).decode("utf-8", "replace")
    out: list[tuple[str, datetime | None]] = []
    for block in URL_BLOCK_RE.findall(xml):
        loc_m = LOC_RE.search(block)
        if not loc_m:
            continue
        loc = loc_m.group(1)
        lastmod = None
        lm = LASTMOD_RE.search(block)
        if lm:
            try:
                lastmod = datetime.fromisoformat(lm.group(1))
            except ValueError:
                lastmod = None
        out.append((loc, lastmod))
    return out


# --------------------------------------------------------------------------- #
# Parsing du prix d'une fiche produit
# --------------------------------------------------------------------------- #

# Dans la page, chaque variante (neuf / occasion) est décrite par un objet JS
# analytics délimité par des accolades qui contient, entre autres :
#   ...,"metric1":14.99,"metric2":79.99,...,"condition":"new",...,"precommande":0,...
# où metric1 = prix actuel et metric2 = prix de référence (barré).
METRIC_RE = re.compile(r'"metric1":(?P<cur>[\d.]+),"metric2":(?P<ref>[\d.]+)')
# Prix VISIBLE (fallback quand l'analytics est strippé) : prix de vente +
# prix barré, chacun dans un <span class="value" ... content="X.XX">.
SALES_PRICE_RE = re.compile(
    r'class="sales[^"]*"[^>]*>\s*<span class="value"[^>]*content="([\d.]+)"',
    re.IGNORECASE,
)
STRIKE_PRICE_RE = re.compile(
    r'class="strike-through[^"]*"[^>]*>\s*<span class="value"[^>]*content="([\d.]+)"',
    re.IGNORECASE,
)
COND_RE = re.compile(r'"condition":"([^"]*)"')
PRECO_RE = re.compile(r'"precommande":(\d+)')
EDITION_RE = re.compile(r'"edition":"([^"]*)"')
PLATFORM_RE = re.compile(r'"platform":"([^"]*)"')
DISPO_RE = re.compile(r'"dispoweb":(\d+)')
IMAGE_RE = re.compile(r'"urlImage":"([^"]+)"')
PEGI_RE = re.compile(r'"rating_pegi":"([^"]*)"')
GENRE_RE = re.compile(r'"genre":"([^"]*)"')
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
OG_TITLE_RE = re.compile(
    r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']',
    re.IGNORECASE | re.DOTALL,
)
OG_IMAGE_RE = re.compile(
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\'](.*?)["\']',
    re.IGNORECASE | re.DOTALL,
)


def _enclosing_object(text: str, pos: int) -> str:
    """Retourne la sous-chaîne {…} contenant l'index pos (matching d'accolades)."""
    start = text.rfind("{", 0, pos)
    if start == -1:
        return ""
    depth = 0
    i = start
    n = len(text)
    while i < n:
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
        i += 1
    return text[start:]


def parse_product(url: str) -> list[dict]:
    """Extrait les variantes (prix actuel/ref) d'une fiche produit."""
    page = http_get(url).decode("utf-8", "replace")
    decoded = html.unescape(page)

    # Titre propre : og:title de préférence, sinon <title> nettoyé.
    title = ""
    ogm = OG_TITLE_RE.search(decoded)
    if ogm:
        title = re.sub(r"\s+", " ", ogm.group(1)).strip()
    if not title:
        tm = TITLE_RE.search(decoded)
        if tm:
            title = re.sub(r"\s+", " ", tm.group(1)).strip()
    # Coupe les suffixes marketing ("... sur PS5, tous les jeux ...", "| Micromania").
    title = re.split(r"\s+sur\s+\w|\s*\|\s*", title)[0].strip()

    page_image = ""
    ogi = OG_IMAGE_RE.search(decoded)
    if ogi:
        page_image = ogi.group(1).strip()

    variants: list[dict] = []
    for m in METRIC_RE.finditer(decoded):
        cur = float(m.group("cur"))
        ref = float(m.group("ref"))
        obj = _enclosing_object(decoded, m.start())
        cond_m = COND_RE.search(obj)
        preco_m = PRECO_RE.search(obj)
        edition_m = EDITION_RE.search(obj)
        platform_m = PLATFORM_RE.search(obj)
        img_m = IMAGE_RE.search(obj)
        pegi_m = PEGI_RE.search(obj)
        genre_m = GENRE_RE.search(obj)
        dispo_m = DISPO_RE.search(obj)
        variants.append(
            {
                "url": url,
                "title": title,
                "condition": cond_m.group(1) if cond_m else "",
                "current": cur,
                "reference": ref,
                "precommande": int(preco_m.group(1)) if preco_m else 0,
                "edition": edition_m.group(1) if edition_m else "",
                "platform": platform_m.group(1) if platform_m else "",
                "image": (img_m.group(1) if img_m else "") or page_image,
                "pegi": pegi_m.group(1) if pegi_m else "",
                "genre": genre_m.group(1) if genre_m else "",
                # dispoweb=1 -> disponible à l'achat sur le web.
                "available": (int(dispo_m.group(1)) == 1) if dispo_m else True,
            }
        )

    # Fallback : si le bloc analytics est absent (page "allégée" par DataDome
    # sur IP datacenter), on lit le PRIX VISIBLE affiché à l'acheteur, qui lui
    # reste présent : <span class="sales ...">prix actuel</span> +
    # <span class="strike-through ...">prix barré</span>.
    if not variants:
        cur_m = SALES_PRICE_RE.search(decoded)
        ref_m = STRIKE_PRICE_RE.search(decoded)
        if cur_m and ref_m:
            cur = float(cur_m.group(1))
            ref = float(ref_m.group(1))
            # rupture web si "créer une alerte" et pas de bouton panier.
            avail = ("ajouter au panier" in decoded.lower()
                     or "retrait" in decoded.lower())
            variants.append(
                {
                    "url": url, "title": title, "condition": "new",
                    "current": cur, "reference": ref, "precommande": 0,
                    "edition": "", "platform": "", "image": page_image,
                    "pegi": "", "genre": "", "available": avail,
                }
            )
    return variants


def is_deal(v: dict) -> bool:
    """Le produit correspond-il aux critères d'alerte ?"""
    if not INCLUDE_USED and v["condition"].lower() not in ("new", "neuf"):
        return False
    if not INCLUDE_PRECOMMANDE and v["precommande"]:
        return False
    if not INCLUDE_UNAVAILABLE and not v.get("available", True):
        return False
    if v["reference"] < MIN_REFERENCE_PRICE:
        return False
    if v["current"] <= 0 or v["reference"] <= 0:
        return False
    discount = 1.0 - (v["current"] / v["reference"])
    return discount >= DISCOUNT_THRESHOLD


def discount_pct(v: dict) -> int:
    return round((1.0 - v["current"] / v["reference"]) * 100)


# --------------------------------------------------------------------------- #
# État (dédup)
# --------------------------------------------------------------------------- #

def load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"last_scan": None, "seen": {}}


def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=True)


# --------------------------------------------------------------------------- #
# Alertes
# --------------------------------------------------------------------------- #

USER_AGENT_BOT = "MicromaniaDealsBot (https://github.com, 1.0)"

# Espacement minimum entre deux notifications, pour éviter le rate-limit
# (utile quand les alertes partent en rafale depuis plusieurs threads).
ALERT_MIN_INTERVAL = float(os.environ.get("ALERT_MIN_INTERVAL", "0.4"))
_alert_lock = threading.Lock()
_last_alert_at = [0.0]


def _throttle() -> None:
    with _alert_lock:
        wait = ALERT_MIN_INTERVAL - (time.monotonic() - _last_alert_at[0])
        if wait > 0:
            time.sleep(wait)
        _last_alert_at[0] = time.monotonic()


def _cond_label(v: dict) -> str:
    return "Neuf" if v["condition"].lower() in ("new", "neuf") else (
        "Occasion" if v["condition"].lower() in ("used", "occasion") else v["condition"]
    )


def _euro(x: float) -> str:
    return f"{x:.2f}".replace(".", ",") + " €"


def _dispo_label(v: dict) -> str:
    return "✅ Dispo web" if v.get("available", True) else "🏬 Retrait magasin / rupture web"


def format_text(v: dict) -> str:
    """Version texte (Telegram / logs / fallback)."""
    pct = discount_pct(v)
    extra = " ".join(filter(None, [v.get("platform"), v.get("edition")])).strip()
    extra = f" ({extra})" if extra else ""
    return (
        f"🔥 DEAL Micromania -{pct}%\n"
        f"🏷 {v['title']}{extra}\n"
        f"💰 {_euro(v['current'])} (au lieu de {_euro(v['reference'])}) — {_cond_label(v)}\n"
        f"{_dispo_label(v)}\n"
        f"🔗 {v['url']}"
    )


def _discord_embed(v: dict) -> dict:
    pct = discount_pct(v)
    # Couleur : rouge vif pour les remises massives, orange sinon.
    color = 0xC0392B if pct >= 70 else 0xE67E22
    fields = [
        {"name": "💰 Prix", "value": f"**{_euro(v['current'])}**", "inline": True},
        {"name": "🏷 Avant", "value": f"~~{_euro(v['reference'])}~~", "inline": True},
        {"name": "📉 Réduction", "value": f"**-{pct}%**", "inline": True},
        {"name": "📦 État", "value": _cond_label(v), "inline": True},
    ]
    if v.get("platform"):
        fields.append({"name": "🎮 Plateforme", "value": v["platform"], "inline": True})
    if v.get("edition"):
        fields.append({"name": "✨ Édition", "value": v["edition"], "inline": True})
    if v.get("pegi"):
        fields.append({"name": "🔞 PEGI", "value": v["pegi"], "inline": True})
    fields.append({"name": "📦 Dispo", "value": _dispo_label(v), "inline": True})

    economy = v["reference"] - v["current"]
    embed = {
        "title": f"🔥 {v['title']}"[:256],
        "url": v["url"],
        "description": (
            f"**{_euro(v['current'])}**  ~~{_euro(v['reference'])}~~   "
            f"•   **-{pct}%**  (tu économises {_euro(economy)})"
        ),
        "color": color,
        "fields": fields,
        "footer": {"text": "Micromania deals watcher"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if v.get("image"):
        embed["image"] = {"url": v["image"]}
    return embed


def _http_post_json(url: str, payload: dict) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            # Discord renvoie 403 sans User-Agent explicite.
            "User-Agent": USER_AGENT_BOT,
        },
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        resp.read()


_og_cache: dict[str, str] = {}
_og_lock = threading.Lock()


def _enrich_image(v: dict) -> None:
    """Récupère l'image officielle (og:image) de la fiche si manquante.
    Appelé seulement pour les DEALS (peu nombreux) -> coût négligeable."""
    if v.get("image") or not v.get("url"):
        return
    u = v["url"]
    with _og_lock:
        if u in _og_cache:
            v["image"] = _og_cache[u]
            return
    img = ""
    try:
        page = html.unescape(http_get(u).decode("utf-8", "replace"))
        mm = IMAGE_RE.search(page) or OG_IMAGE_RE.search(page)
        if mm:
            img = mm.group(1).strip()
    except Exception:  # noqa: BLE001
        pass
    with _og_lock:
        _og_cache[u] = img
    v["image"] = img


def _send_discord(v: dict) -> None:
    webhook = _webhook_for(v)
    if not webhook:
        return
    if not v.get("image"):
        _enrich_image(v)
    embed = _discord_embed(v)
    button = {
        "type": 1,
        "components": [
            {"type": 2, "style": 5, "label": "🛒 Voir le deal", "url": v["url"]}
        ],
    }
    # Essai avec bouton lien ; repli sur embed seul si le webhook le refuse.
    try:
        _http_post_json(webhook, {"embeds": [embed], "components": [button]})
    except Exception:  # noqa: BLE001
        _http_post_json(webhook, {"embeds": [embed]})


def _send_telegram(v: dict) -> None:
    base = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    caption = format_text(v)
    markup = {
        "inline_keyboard": [[{"text": "🛒 Voir le deal", "url": v["url"]}]]
    }
    if v.get("image"):
        _http_post_json(
            f"{base}/sendPhoto",
            {
                "chat_id": TELEGRAM_CHAT_ID,
                "photo": v["image"],
                "caption": caption,
                "reply_markup": markup,
            },
        )
    else:
        _http_post_json(
            f"{base}/sendMessage",
            {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": caption,
                "reply_markup": markup,
                "disable_web_page_preview": False,
            },
        )


def send_alert(v: dict) -> None:
    text = format_text(v)
    if DRY_RUN:
        print("[DRY_RUN] " + text.replace("\n", " | "))
        return

    if ANY_DISCORD:
        try:
            _throttle()
            _send_discord(v)
        except Exception as err:  # noqa: BLE001
            print(f"[discord] échec: {err}", file=sys.stderr)

    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            _send_telegram(v)
        except Exception as err:  # noqa: BLE001
            print(f"[telegram] échec: {err}", file=sys.stderr)

    # Toujours journaliser (stdout + fichier).
    print(text)
    print("-" * 60)
    try:
        os.makedirs(os.path.dirname(DEALS_LOG) or ".", exist_ok=True)
        with open(DEALS_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now(timezone.utc).isoformat()}\n{text}\n\n")
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def run_once(force_full: bool = False, extra_only: bool = False) -> int:
    """Un scan.

    Source des prix = les TUILES des pages catégorie (fiables sur le VPS).
      - extra_only=True  : seulement FAST_CATEGORIES (packs/collectors/premium).
      - sinon            : FAST_CATEGORIES + FULL_CATEGORIES (tous supports).
    Plus l'énumération des packs par ID (/mbN.html), et le sitemap si activé.
    """
    state = load_state()
    now = datetime.now(timezone.utc)
    seen: dict = state.get("seen", {})
    mb_seen: list[int] = []
    stats = {"products": 0, "deals": 0}

    kind = "RAPIDE (packs/collectors)" if extra_only else "COMPLET (tous supports)"
    print(f"== Micromania deals == {now.isoformat()} — passage {kind}")
    print(f"Seuil: -{int(DISCOUNT_THRESHOLD*100)}% | prix réf. >= {MIN_REFERENCE_PRICE:.0f}€")

    def handle(v: dict) -> None:
        """Détection + dédup + alerte pour un produit."""
        stats["products"] += 1
        mm = MB_URL_RE.search(v["url"])
        if mm and v.get("reference", 0) > 0:
            mb_seen.append(int(mm.group(1)))
        if not is_deal(v):
            return
        key = v["url"] + "#" + v.get("condition", "")
        prev = seen.get(key)
        if prev is not None and v["current"] >= float(prev):
            return  # déjà alerté à ce prix (ou plus bas)
        send_alert(v)
        seen[key] = v["current"]
        stats["deals"] += 1

    # 1) Détection des prix via les TUILES des pages catégorie.
    cats = list(FAST_CATEGORIES)
    if not extra_only:
        cats += [c for c in FULL_CATEGORIES if c not in cats]

    def scan_cat(slug: str) -> list[dict]:
        try:
            tiles = parse_category_tiles(slug)
            t = _slug_type(slug)
            for v in tiles:
                v["type"] = t
            return tiles
        except Exception as err:  # noqa: BLE001
            print(f"[catégorie] {slug}: {err}", file=sys.stderr)
            return []

    with ThreadPoolExecutor(max_workers=CATEGORY_CONCURRENCY) as pool:
        for fut in as_completed([pool.submit(scan_cat, s) for s in cats]):
            for v in fut.result():
                handle(v)
            sd_notify("WATCHDOG=1")
    print(f"Catégories scannées: {len(cats)} | produits vus: {stats['products']}")

    # 2) URLs produits via parse_product : packs (par ID) + sitemap optionnel.
    #    UNIQUEMENT au passage complet : ces sources sont volumineuses (740 IDs
    #    de packs + 13k fiches) et martèleraient l'anti-bot à chaque passage.
    url_candidates: set[str] = set()
    if PACK_ID_ENUM and not extra_only:
        floor = max(int(state.get("pack_id_max", 0)), 700)
        mx = PACK_ID_MAX or (floor + PACK_ID_BUFFER)
        url_candidates |= {f"{SITE_ROOT}/mb{n}.html" for n in range(1, mx + 1)}
        print(f"Énumération packs: mb1..mb{mx}")
    if SCAN_SITEMAP and not extra_only:
        for sm in get_product_sitemaps():
            try:
                for loc, _ in parse_sitemap(sm):
                    url_candidates.add(loc)
            except Exception as err:  # noqa: BLE001
                print(f"[sitemap] {sm}: {err}", file=sys.stderr)

    def worker(u: str) -> list[dict]:
        try:
            return parse_product(u)
        except Exception as err:  # noqa: BLE001
            msg = str(err)
            if not any(c in msg for c in ("HTTP 410", "HTTP 404", "HTTP 403")):
                print(f"[produit] {u}: {err}", file=sys.stderr)
            return []

    if url_candidates:
        done = 0
        with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
            for fut in as_completed([pool.submit(worker, u) for u in url_candidates]):
                done += 1
                for v in fut.result():
                    handle(v)
                if done % 250 == 0:
                    sd_notify("WATCHDOG=1")

    # 3) Sauvegarde de l'état (dont la frontière d'IDs de packs).
    state["seen"] = seen
    if mb_seen:
        state["pack_id_max"] = max(int(state.get("pack_id_max", 0)), max(mb_seen))
    if not extra_only:
        state["last_scan"] = now.isoformat()
    save_state(state)

    print(f"Terminé: {stats['products']} produits inspectés, {stats['deals']} alerte(s).")
    return 0


def main() -> int:
    if not LOOP_ENABLED:
        return run_once()

    # Mode boucle à deux vitesses :
    #  - chaque itération : passage RAPIDE (packs + collectors + énum. d'IDs) ;
    #  - toutes les FULL_CATALOG_EVERY_MINUTES : passage COMPLET (catalogue).
    deadline = (time.monotonic() + LOOP_MAX_SECONDS) if LOOP_MAX_SECONDS > 0 else None
    full_every = FULL_CATALOG_EVERY_MINUTES * 60
    last_full = 0.0  # 0 => le 1er passage est un scan complet
    duree = "illimité" if deadline is None else f"~{LOOP_MAX_SECONDS // 60} min"
    print(
        f"Mode BOUCLE : passages rapides (packs/collectors) toutes les "
        f"~{LOOP_INTERVAL_SECONDS}s, scan COMPLET toutes les "
        f"{FULL_CATALOG_EVERY_MINUTES} min, durée {duree}."
    )
    sd_notify("READY=1")  # informe systemd que le service est prêt
    while True:
        start = time.monotonic()
        do_full = (start - last_full) >= full_every
        try:
            run_once(force_full=do_full, extra_only=not do_full)
            if do_full:
                last_full = start
        except Exception as err:  # noqa: BLE001 - la boucle ne doit pas mourir
            print(f"[boucle] erreur de scan: {err}", file=sys.stderr)
        sd_notify("WATCHDOG=1")  # heartbeat : "je suis vivant"
        if deadline is not None and time.monotonic() >= deadline:
            print("Fin de la fenêtre de boucle.")
            return 0
        time.sleep(LOOP_INTERVAL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
