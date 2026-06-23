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
    "jeux-ps5,jeux-xbox,jeux-switch,jeux-switch2,jeux-ps4,jeux-pc,"
    "figurines,tous-les-produits-derives,mugs-et-verres,peluches,sacs,"
    # Accessoires / périphériques (slug Micromania en ANGLAIS "accessories") :
    # tapis de souris, claviers, casques, manettes, écouteurs, chargeurs…
    # Micromania n'a PAS de catégorie accessoires par console ; tout est groupé
    # ici. Slugs confirmés avec produits + prix :
    #   accessories-pc (≈630), steelseries-2 (≈130, casques/souris),
    #   nacon (manettes consoles), setup-gaming (matériel gaming).
    "accessories-pc,steelseries-2,nacon,setup-gaming,"
    # Cartes à collectionner (la cat. "cartes" couvre Pokémon/Lorcana/… ;
    # cartespokemon + packs rares = focus Pokémon). Les cartes rares s'arrachent.
    "cartes,cartespokemon,packs-rares-pokemon-cartes-authentiques",
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

# SITEMAP INCRÉMENTAL (capte les ÉPHÉMÈRES/EXCLUS absents des listings) : à
# chaque passage on compare les fiches du sitemap à celles déjà vues ; les
# NOUVELLES (nouveaux IDs) sont vérifiées via la recherche (prix + lien /p/).
# 1er passage = on mémorise tout sans scanner (sinon 13k recherches). Ensuite
# seules les vraies nouveautés (quelques-unes/jour) sont vérifiées -> léger.
SCAN_SITEMAP_NEW = os.environ.get("SCAN_SITEMAP_NEW", "true").lower() == "true"
SITEMAP_NEW_MAX = int(os.environ.get("SITEMAP_NEW_MAX", "150"))  # garde-fou/passage
SITEMAP_INTERVAL_MIN = int(os.environ.get("SITEMAP_INTERVAL_MIN", "30"))  # cadence

# Énumération des packs par ID : /mbN.html redirige vers la fiche du pack
# (même pour des packs éphémères jamais listés dans une catégorie). On sonde
# toute la plage mb1..mbMAX pour ne rater aucun pack flash / erreur de prix.
# Désactivé par défaut : 740 requêtes /mbN.html d'un coup font challenger
# l'anti-bot (et risquent de flaguer l'IP). Les packs listés sont déjà couverts
# par la catégorie tous-nos-packs. À n'activer qu'avec un proxy costaud.
# Énumération DOUCE : on ne sonde que la fenêtre des IDs récents (là où
# apparaissent les nouveaux packs éphémères), pas toute la plage -> pas de ban.
PACK_ID_ENUM = os.environ.get("PACK_ID_ENUM", "true").lower() == "true"
PACK_ID_MAX = int(os.environ.get("PACK_ID_MAX", "0"))  # 0 = auto (frontière)
PACK_ID_BUFFER = int(os.environ.get("PACK_ID_BUFFER", "40"))  # IDs sondés au-dessus
PACK_ID_LOOKBACK = int(os.environ.get("PACK_ID_LOOKBACK", "30"))  # et en-dessous
# Balayage TOURNANT : à chaque passage on sonde aussi un bout de la plage
# complète (qui avance), pour couvrir TOUS les IDs au fil du temps sans ban.
PACK_ROLL_CHUNK = int(os.environ.get("PACK_ROLL_CHUNK", "60"))
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
# Routage par TRANCHES DE % DE RÉDUCTION (prioritaire sur le prix).
# DISCOUNT_TIERS = bornes croissantes en %, ex "60,80" -> 3 tranches :
#   <60% / 60-80% / >=80%. DISCORD_WEBHOOK_DISCOUNT_TIERS = 1 webhook/tranche.
DISCOUNT_TIERS = [
    float(x) for x in os.environ.get("DISCOUNT_TIERS", "").split(",") if x.strip()
]
DISCOUNT_TIER_WEBHOOKS = [
    w.strip()
    for w in os.environ.get("DISCORD_WEBHOOK_DISCOUNT_TIERS", "").split(",")
]

# Sur quel prix router les tranches : "current" = prix de VENTE (après remise),
# "reference" = prix barré. Pour "tout ce qui est à ~2€", mettre "current".
ROUTE_PRICE_FIELD = os.environ.get("ROUTE_PRICE_FIELD", "reference").lower()

# Routage par TRANCHES DE PRIX.
# PRICE_TIERS = bornes croissantes, ex "20,80" -> 3 tranches : <20 / 20-80 / >=80
# DISCORD_WEBHOOK_TIERS = un webhook par tranche (séparés par des virgules),
# dans le même ordre. Une tranche vide retombe sur DISCORD_WEBHOOK_URL.
PRICE_TIERS = [
    float(x) for x in os.environ.get("PRICE_TIERS", "").split(",") if x.strip()
]
TIER_WEBHOOKS = [
    w.strip() for w in os.environ.get("DISCORD_WEBHOOK_TIERS", "").split(",")
]

# Routage par CATÉGORIE. Un deal va aussi dans son salon de catégorie (en plus
# de son salon de prix => il peut apparaître dans 2 salons : double envoi).
# Jeux séparés par plateforme : PS5 / PS4 / Xbox / Switch / PC.
def _wh(name: str) -> str:
    return os.environ.get(name, "").strip()


WEBHOOK_PEPITES = _wh("DISCORD_WEBHOOK_PEPITES")
PEPITE_MIN = float(os.environ.get("PEPITE_MIN", "80"))
CATEGORY_WEBHOOKS = {
    "ps5": _wh("DISCORD_WEBHOOK_PS5"),
    "ps4": _wh("DISCORD_WEBHOOK_PS4"),
    "xbox": _wh("DISCORD_WEBHOOK_XBOX"),
    "switch": _wh("DISCORD_WEBHOOK_SWITCH"),
    "pc": _wh("DISCORD_WEBHOOK_PC"),
    # repli "jeux" si tu veux UN seul salon jeux au lieu de par plateforme
    "jeux": _wh("DISCORD_WEBHOOK_JEUX"),
    "figurines": _wh("DISCORD_WEBHOOK_FIGURINES"),
    "collector": _wh("DISCORD_WEBHOOK_COLLECTOR"),
    "goodies": _wh("DISCORD_WEBHOOK_GOODIES"),
}

def _deal_type(v: dict, slug: str = "") -> str:
    """Type de deal d'après les infos PRODUIT (plateforme/titre/édition),
    plus fiable que la seule catégorie source.
    -> ps5 / ps4 / xbox / switch / pc / collector / figurines / goodies."""
    s = slug.lower()
    title = (v.get("title") or "").lower()
    edition = (v.get("edition") or "").lower()
    # 1) Collector explicite (édition collector/limitée, packs, catégorie dédiée).
    if (
        "collector" in edition
        or "collector" in title
        or "edition limitee" in title
        or s == "jeux-video-edition-collector"
        or "tous-nos-packs" in s
    ):
        return "collector"
    # 2) Jeu sur console -> par plateforme.
    plat = (v.get("platform") or "").lower()
    if "ps5" in plat:
        return "ps5"
    if "ps4" in plat:
        return "ps4"
    if "xbox" in plat:
        return "xbox"
    if "switch" in plat:
        return "switch"
    if plat == "pc":
        return "pc"
    # 2b) Accessoire SANS champ platform (Micromania ne tague pas la console sur
    # les accessoires) : on déduit la console depuis le TITRE. Ex : "Manette
    # PS5 Nacon", "Casque Xbox", "Étui Switch", "Tapis de souris PC".
    for kw, typ in (
        (r"\bps5\b", "ps5"), (r"\bps4\b", "ps4"), (r"\bxbox\b", "xbox"),
        (r"\bswitch\b", "switch"), (r"\bpc\b", "pc"),
    ):
        if re.search(kw, title):
            return typ
    if s.startswith("jeux-"):  # jeu sans plateforme nette
        return s.replace("jeux-", "", 1) if s.replace("jeux-", "", 1) in (
            "ps5", "ps4", "xbox", "switch", "pc"
        ) else "goodies"
    # 3) Figurine / goodie.
    if "figurine" in title or "figurine" in s:
        return "figurines"
    return "goodies"


def _destinations(v: dict) -> list[str]:
    """Tous les salons où envoyer ce deal (catégorie ET prix/% => double envoi).
    Si rien ne matche, repli sur le salon par défaut."""
    ref = v.get("reference", 0)
    cur = v.get("current", 0)
    dests: list[str] = []

    def add(w: str) -> None:
        if w and w not in dests:
            dests.append(w)

    # a) Salon de catégorie (repli sur un salon "jeux" global si la plateforme
    #    n'a pas son propre salon).
    typ = v.get("type", "")
    cwh = CATEGORY_WEBHOOKS.get(typ, "")
    if not cwh and typ in ("ps5", "ps4", "xbox", "switch", "pc"):
        cwh = CATEGORY_WEBHOOKS.get("jeux", "")
    add(cwh)
    # b) Salon par % de réduction.
    if DISCOUNT_TIERS and any(DISCOUNT_TIER_WEBHOOKS) and ref > 0:
        idx = sum(1 for b in DISCOUNT_TIERS if (1 - cur / ref) * 100 >= b)
        if idx < len(DISCOUNT_TIER_WEBHOOKS):
            add(DISCOUNT_TIER_WEBHOOKS[idx])
    # c) Salon par tranche de prix (de vente ou barré).
    if PRICE_TIERS and any(TIER_WEBHOOKS):
        val = cur if ROUTE_PRICE_FIELD == "current" else ref
        idx = sum(1 for b in PRICE_TIERS if val >= b)
        if idx < len(TIER_WEBHOOKS):
            add(TIER_WEBHOOKS[idx])
    # d) Salon "pépites".
    if WEBHOOK_PEPITES and ref >= PEPITE_MIN:
        add(WEBHOOK_PEPITES)

    if not dests:
        add(DISCORD_WEBHOOK_URL)
    return dests


ANY_DISCORD = bool(
    DISCORD_WEBHOOK_URL
    or any(DISCOUNT_TIER_WEBHOOKS)
    or any(TIER_WEBHOOKS)
    or any(CATEGORY_WEBHOOKS.values())
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

# Proxy(s) RÉSIDENTIEL(s) pour contourner le bannissement Incapsula des IP
# datacenter ET répartir la charge sur plusieurs IP (donc scanner VITE sans
# bannissement).
#   • PROXY       = un seul proxy        -> http://user:pass@host:port
#   • PROXY_LIST  = plusieurs proxies    -> séparés par virgule, espace, ou
#                   saut de ligne. Ex (Webshare static residential, 20 IP) :
#                   PROXY_LIST="http://user:pass@195.40.128.56:6776,http://user:pass@192.53.70.90:5804,..."
#                   Format "host:port:user:pass" (export Webshare) aussi accepté.
# Chaque thread/session pioche un proxy différent (round-robin) : les 20 IP
# tournent et se partagent les requêtes -> cadence élevée, charge divisée par 20.
def _parse_proxy_entry(raw: str) -> str:
    """Normalise une entrée proxy en URL http://user:pass@host:port."""
    raw = raw.strip()
    if not raw:
        return ""
    if "://" in raw:
        return raw
    parts = raw.split(":")
    # Format Webshare exporté : host:port:user:pass
    if len(parts) == 4:
        host, port, user, pwd = parts
        return f"http://{user}:{pwd}@{host}:{port}"
    # Format host:port (sans auth)
    if len(parts) == 2:
        return f"http://{raw}"
    return "http://" + raw


_proxy_raw = os.environ.get("PROXY_LIST", "") or os.environ.get("PROXY", "")
PROXY_POOL = [
    _parse_proxy_entry(p)
    for p in re.split(r"[,\s]+", _proxy_raw.strip())
    if p.strip()
]
# Compat : variable globale pour les usages restants (1er proxy du pool).
PROXY = PROXY_POOL[0] if PROXY_POOL else ""
_proxy_rr = [0]
_proxy_rr_lock = threading.Lock()


def _next_proxy_url():
    """Renvoie l'URL du prochain proxy (round-robin sur le pool), ou None si
    aucun proxy configuré."""
    if not PROXY_POOL:
        return None
    with _proxy_rr_lock:
        url = PROXY_POOL[_proxy_rr[0] % len(PROXY_POOL)]
        _proxy_rr[0] += 1
    return url


def _next_proxies():
    """Mapping proxies curl_cffi pour la prochaine session (compat)."""
    url = _next_proxy_url()
    return {"http": url, "https": url} if url else None

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


def _new_session(direct: bool = False):
    """Crée une session curl_cffi réchauffée. direct=True -> connexion DIRECTE
    (sans proxy, donc l'IP de la machine/téléphone), utilisée pour aller chercher
    les images sur les fiches : peu de requêtes, et ça épargne les proxies."""
    if direct:
        proxies = None
        _tls.direct_key = "DIRECT"
    else:
        url = _next_proxy_url()
        _tls.proxy_url = url or ""   # mémorise le proxy de cette session (clé débit)
        proxies = {"http": url, "https": url} if url else None
    s = cffi.Session(impersonate=IMPERSONATE, proxies=proxies)
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
            n = len(PROXY_POOL)
            via = f" via {n} proxies (rotation)" if n else ""
            print(f"[warmup] session curl_cffi réchauffée (home à froid){via}")
    return s


def _session(direct: bool = False):
    attr = "sd" if direct else "s"
    s = getattr(_tls, attr, None)
    if s is None:
        s = _new_session(direct=direct)
        setattr(_tls, attr, s)
    return s


# Limiteur de débit ADAPTATIF : le bot trouve tout seul la vitesse max que
# l'anti-bot tolère. À chaque blocage il RALENTIT ; quand tout va bien il
# ré-accélère doucement. Résultat : il scanne en continu, sans jamais se faire
# bannir durablement, sans intervention.
RATE_MIN = float(os.environ.get("RATE_MIN", "0.3"))   # intervalle plancher (rapide)
RATE_MAX = float(os.environ.get("RATE_MAX", "4.0"))   # intervalle plafond (lent)
RATE_START = float(os.environ.get("RATE_START", "0.5"))
_rate_lock = threading.Lock()
# Limiteur PAR PROXY : chaque IP a son propre intervalle adaptatif. Un proxy
# bloqué ralentit SEULEMENT lui-même ; les 19 autres continuent à fond, en
# parallèle. Le débit total = somme des débits de chaque IP saine.
# clé = URL du proxy ("" si aucun proxy = mode IP unique).
_rate_state: dict[str, dict] = {}


def _rate_for(key: str) -> dict:
    st = _rate_state.get(key)
    if st is None:
        st = {"interval": RATE_START, "last": 0.0, "streak": 0}
        _rate_state[key] = st
    return st


def _rate_gate(key: str = "") -> None:
    """Espace les requêtes d'UN proxy selon son intervalle adaptatif propre."""
    with _rate_lock:
        st = _rate_for(key)
        now = time.monotonic()
        target = max(now, st["last"] + st["interval"])
        st["last"] = target
    delay = target - time.monotonic()
    if delay > 0:
        time.sleep(delay)


def _note_block(key: str = "") -> None:
    """Blocage sur ce proxy -> on ralentit CE proxy uniquement (×1.6)."""
    with _rate_lock:
        st = _rate_for(key)
        st["streak"] = 0
        old = st["interval"]
        st["interval"] = min(st["interval"] * 1.6, RATE_MAX)
        newv = st["interval"]
    if newv != old:
        tag = key.rsplit("@", 1)[-1] if key else "ip"
        print(
            f"[auto-débit] {tag} ralenti → {newv:.2f}s/req (anti-ban)",
            file=sys.stderr,
        )


def _note_ok(key: str = "") -> None:
    """Succès sur ce proxy -> il ré-accélère doucement après une bonne série."""
    with _rate_lock:
        st = _rate_for(key)
        st["streak"] += 1
        if st["streak"] >= 40 and st["interval"] > RATE_MIN:
            st["streak"] = 0
            st["interval"] = max(st["interval"] * 0.9, RATE_MIN)


def http_get(url: str, retries: int = 3, direct: bool = False) -> bytes:
    """GET résistant à Incapsula : session réchauffée + auto-régulation
    (limiteur de débit + pause automatique en cas de blocage).

    direct=True -> passe par la connexion DIRECTE (IP machine/téléphone) au lieu
    des proxies : réservé à l'enrichissement image (peu de requêtes), ça épargne
    les proxies. Si l'IP directe est freinée, on n'insiste pas (l'image saute,
    le scan via proxies continue)."""
    last_err: Exception | None = None
    sess_attr = "sd" if direct else "s"

    if HAVE_CFFI:
        for attempt in range(retries):
            try:
                sess = _session(direct=direct)  # fixe _tls.proxy_url / direct_key
                key = "DIRECT" if direct else getattr(_tls, "proxy_url", "")
                _rate_gate(key)
                r = sess.get(url, timeout=REQUEST_TIMEOUT)
                if r.status_code in (404, 410):
                    raise RuntimeError(f"GET {url}: HTTP {r.status_code}")
                if r.status_code == 403 or _is_challenge(r.content):
                    # Bloqué : session neuve (autre proxy en rotation) + on
                    # ralentit CE proxy / l'IP directe seulement.
                    setattr(_tls, sess_attr, None)
                    _note_block(key)
                    last_err = RuntimeError("blocage anti-bot (challenge)")
                    time.sleep(1.0 * (attempt + 1))
                    continue
                if r.status_code >= 400:
                    last_err = RuntimeError(f"HTTP {r.status_code}")
                    time.sleep(1.0 * (attempt + 1))
                    continue
                _note_ok(key)  # succès -> ce proxy/IP ré-accélère doucement
                _tls.last_url = str(getattr(r, "url", "") or url)  # URL finale (redirections)
                return r.content
            except RuntimeError:
                raise
            except Exception as err:  # noqa: BLE001
                key = "DIRECT" if direct else getattr(_tls, "proxy_url", "")
                setattr(_tls, sess_attr, None)  # session cassée -> on repart
                _note_block(key)  # curl(16) = blocage déguisé -> compte aussi
                last_err = err
                time.sleep(1.0 * (attempt + 1))
        raise RuntimeError(f"GET échoué pour {url}: {last_err}")

    # --- Repli urllib (IP non bloquée) ---
    _opener = None
    if PROXY_POOL:
        _p = _next_proxies()
        _opener = urllib.request.build_opener(urllib.request.ProxyHandler(_p))
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={**_BROWSER_HEADERS, "Accept-Encoding": "gzip"},
            )
            _open = _opener.open if _opener else urllib.request.urlopen
            with _open(req, timeout=REQUEST_TIMEOUT) as resp:
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
# Capte l'ID de pack dans les 2 formes : /mbN.html (brut) ET /...-mbN.html
# (URL canonique après redirection).
MB_URL_RE = re.compile(r"[/-]mb(\d+)\.html")


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
            "image": _clean_img(i.group(1)) if (i := IMAGE_RE.search(obj)) else "",
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


def _clean_img(u: str) -> str:
    """Nettoie une URL d'image issue du JSON embarqué : les slashes y sont
    échappés (https:\\/\\/...) et Discord refuse alors de charger l'image.
    On dé-échappe \\/ -> / (et \\uXXXX éventuels)."""
    u = (u or "").strip()
    if not u:
        return ""
    u = u.replace("\\/", "/")
    if "\\u" in u:
        try:
            u = u.encode("utf-8").decode("unicode_escape")
        except Exception:  # noqa: BLE001
            pass
    # URL protocole-relative (//...) -> https://
    if u.startswith("//"):
        u = "https:" + u
    return u


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

    # GARDE-FOU : un pack/fiche peut REDIRIGER vers une page CATÉGORIE (ex pack
    # éphémère expiré -> /c/jeux-switch). Il ne faut PAS lire tous les produits
    # de cette page en leur collant l'URL du pack (liens cassés). On détecte la
    # redirection vers une catégorie OU une page-listing (beaucoup de produits)
    # et on abandonne : ces produits sont déjà couverts par le scan catégorie.
    final_url = getattr(_tls, "last_url", "") or url
    # Si la requête a ATTERRI sur une page CATÉGORIE/listing (ex : un pack
    # éphémère dont le /mbN.html redirige vers /c/jeux-switch, ou un listing avec
    # beaucoup de produits), on NE colle PAS l'URL du pack à tous (liens cassés).
    # On extrait chaque produit avec SON vrai lien /p/ via les tuiles -> on GARDE
    # la détection (utile : ces produits ne sont pas toujours dans les pages
    # scannées) ET on a le BON lien.
    if ("/c/" in final_url and "/c/" not in url) or len(set(GID_RE.findall(decoded))) > 30:
        out: dict = {}
        _extract_tiles(page, out)
        items = list(out.values())
        if items:                       # listing AVEC données -> produits + vrai /p/
            for v in items:
                v["type"] = _deal_type(v, "")
            return items
        # Sinon : page « collection » en JavaScript (aucune tuile lisible, ex
        # /c/tmnt). On NE perd PAS la détection : on continue en parsing normal
        # (prix visible) ci-dessous, et _resolve_link retrouvera le vrai /p/.
    # Lien CANONIQUE : un /mbN.html valide redirige vers la vraie fiche du pack
    # (/...-mbN.html). On garde cette URL finale -> lien propre et durable vers
    # le pack éphémère, plutôt que le /mbN.html brut.
    link = final_url if final_url.endswith(".html") else url

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
        page_image = _clean_img(ogi.group(1))

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
                "url": link,
                "title": title,
                "condition": cond_m.group(1) if cond_m else "",
                "current": cur,
                "reference": ref,
                "precommande": int(preco_m.group(1)) if preco_m else 0,
                "edition": edition_m.group(1) if edition_m else "",
                "platform": platform_m.group(1) if platform_m else "",
                "image": (_clean_img(img_m.group(1)) if img_m else "") or page_image,
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
                    "url": link, "title": title, "condition": "new",
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
    stores = v.get("store_stock") or []
    store_line = ""
    if CHECK_STORE_STOCK:
        store_line = (
            f"🏪 En magasin : ✅ {', '.join(stores)}\n" if stores
            else "🏪 En magasin : ❌ pas en stock près de chez toi\n"
        )
    return (
        f"🔥 DEAL Micromania -{pct}%\n"
        f"🏷 {v['title']}{extra}\n"
        f"💰 {_euro(v['current'])} (au lieu de {_euro(v['reference'])}) — {_cond_label(v)}\n"
        f"{_dispo_label(v)}\n"
        f"{store_line}"
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
    if CHECK_STORE_STOCK:
        stores = v.get("store_stock") or []
        fields.append({
            "name": "🏪 En magasin",
            "value": ("✅ " + ", ".join(stores)) if stores
                     else "❌ Pas en stock près de chez toi",
            "inline": False,
        })

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
    img = _clean_img(v.get("image", ""))
    if img.startswith("http"):
        embed["image"] = {"url": img}
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


# Aller chercher l'image sur la FICHE produit (page très protégée) coûte une
# requête lourde par deal. À seuil bas (beaucoup de deals) ça martèle les
# proxies et Micromania les freine. ENRICH_IMAGES=false -> on n'utilise QUE
# l'image déjà présente dans la page catégorie (aucune requête en plus), au prix
# de quelques alertes sans image. Recommandé si tu scannes large (seuil bas).
ENRICH_IMAGES = os.environ.get("ENRICH_IMAGES", "true").lower() in (
    "1", "true", "yes", "on"
)
# IMAGE_VIA_DIRECT=true -> chercher l'image via la connexion DIRECTE (IP de la
# machine) plutôt que les proxies. UTILE seulement si l'IP directe n'est PAS
# bannie. Sur le téléphone (IP bannie) on laisse FALSE : tout passe par les
# proxies.
IMAGE_VIA_DIRECT = os.environ.get("IMAGE_VIA_DIRECT", "false").lower() in (
    "1", "true", "yes", "on"
)


def _enrich_image(v: dict) -> None:
    """Récupère l'image officielle (og:image) de la fiche si manquante.
    Désactivé si ENRICH_IMAGES=false (évite de marteler les fiches)."""
    if not ENRICH_IMAGES or v.get("image") or not v.get("url"):
        return
    u = v["url"]
    with _og_lock:
        if u in _og_cache:
            v["image"] = _og_cache[u]
            return
    img = ""
    # Image via l'IP directe (téléphone) si des proxies existent -> on les épargne.
    use_direct = bool(PROXY_POOL) and IMAGE_VIA_DIRECT
    try:
        page = html.unescape(http_get(u, direct=use_direct).decode("utf-8", "replace"))
        mm = IMAGE_RE.search(page) or OG_IMAGE_RE.search(page)
        if mm:
            img = _clean_img(mm.group(1))
    except Exception:  # noqa: BLE001
        pass
    with _og_lock:
        _og_cache[u] = img
    v["image"] = img


# --------------------------------------------------------------------------- #
# Stock EN MAGASIN (dispo près de chez toi)
# API Micromania : Stores-getAtsValue?pid=<ID>&storeId=<ID magasin> -> JSON
#   {"atsValue": N, "product": {"available": true/false, ...}}
# On interroge chaque magasin surveillé (STORE_IDS) et on liste ceux qui ont le
# produit en stock. Seulement pour les DEALS (peu nombreux) si CHECK_STORE_STOCK.
# --------------------------------------------------------------------------- #
CHECK_STORE_STOCK = os.environ.get("CHECK_STORE_STOCK", "false").lower() in (
    "1", "true", "yes", "on"
)
# Magasins surveillés : "ID:Nom" séparés par virgule.
# Ex (Nice) : NJ:Nice Médecin,ET:Nice Étoile,NT:Nice TNL,NL:Lingostière
STORE_MAP: dict[str, str] = {}
for _part in os.environ.get("STORE_IDS", "").split(","):
    _part = _part.strip()
    if not _part:
        continue
    if ":" in _part:
        _i, _n = _part.split(":", 1)
        STORE_MAP[_i.strip().upper()] = _n.strip()
    else:
        STORE_MAP[_part.upper()] = _part.upper()
_ATS_BASE = (
    SITE_ROOT + "/on/demandware.store/Sites-Micromania-Site/fr_FR/Stores-getAtsValue"
)
_PID_RE = re.compile(r"-(\d+)\.html")
_store_cache: dict[str, list[str]] = {}
_store_lock = threading.Lock()


def _store_fetch(url: str) -> bytes:
    """GET léger pour l'API stock : réponses JSON COURTES (~300 o), donc on NE
    rejette PAS sur la taille (contrairement à http_get) ; on rejette seulement
    une page-challenge Incapsula explicite."""
    sess = _session()
    key = getattr(_tls, "proxy_url", "")
    _rate_gate(key)
    r = sess.get(url, timeout=REQUEST_TIMEOUT)
    c = r.content
    if r.status_code != 200 or b"_Incapsula_Resource" in c[:500]:
        _tls.s = None
        _note_block(key)
        raise RuntimeError("stock: bloqué/erreur")
    _note_ok(key)
    return c


def _store_stock(v: dict) -> list[str]:
    """Liste des magasins surveillés où le produit est EN STOCK."""
    if not CHECK_STORE_STOCK or not STORE_MAP:
        return []
    m = _PID_RE.search(v.get("url") or "")
    if not m:
        return []
    pid = m.group(1)
    with _store_lock:
        if pid in _store_cache:
            return _store_cache[pid]
    dispo: list[str] = []
    for sid, name in STORE_MAP.items():
        try:
            d = json.loads(
                _store_fetch(f"{_ATS_BASE}?pid={pid}&storeId={sid}").decode(
                    "utf-8", "replace"
                )
            )
            ats = d.get("atsValue") or 0
            avail = bool((d.get("product") or {}).get("available"))
            if ats > 0 or avail:
                dispo.append(name)
        except Exception:  # noqa: BLE001
            pass
    with _store_lock:
        _store_cache[pid] = dispo
    return dispo


# Mots trop communs pour identifier un produit (on ne cherche pas dessus).
_STOP_WORDS = {
    "edition", "collector", "collectors", "exclusivite", "exclusivites",
    "micromania", "pack", "deluxe", "standard", "limitee", "limited", "jeu",
    "game", "the", "and", "pour", "nintendo", "switch", "playstation", "xbox",
    "hot", "vol", "box", "set", "premium", "ultimate", "special",
}
_SEARCH_URL = (
    SITE_ROOT + "/on/demandware.store/Sites-Micromania-Site/fr_FR/Search-Show"
)
_resolve_cache: dict[str, str] = {}
_resolve_lock = threading.Lock()


def _resolve_link(v: dict) -> None:
    """Si le lien n'est PAS une fiche /p/ propre (ex page collection /c/...),
    retrouve le vrai /p/ via la recherche : on cherche le MOT le plus distinctif
    du titre, puis on garde le lien qui partage le plus de mots avec le titre
    (méthode prouvée : « splintered » -> bon /p/). Sinon on garde le lien actuel
    (qui ouvre quand même le deal)."""
    url = v.get("url", "")
    if "/p/" in url:
        return  # déjà une fiche propre
    title = v.get("title") or ""
    words = [w for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) > 3]
    distinctive = [w for w in words if w not in _STOP_WORDS]
    if not distinctive:
        return
    q = max(distinctive, key=len)  # le mot le plus distinctif
    with _resolve_lock:
        if q in _resolve_cache:
            _best_from(v, words, _resolve_cache[q])
            return
    try:
        page = http_get(f"{_SEARCH_URL}?q={q}").decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return
    with _resolve_lock:
        _resolve_cache[q] = page
    _best_from(v, words, page)


def _best_from(v: dict, words: list[str], page: str) -> None:
    links = set(re.findall(r"/p/[a-z0-9\-]+\.html", page))
    if not links:
        return
    best = max(links, key=lambda l: sum(1 for w in words if w in l))
    if sum(1 for w in words if w in best) >= 3:  # match solide
        v["url"] = SITE_ROOT + best


def _send_discord(v: dict) -> None:
    dests = _destinations(v)
    if not dests:
        return
    _resolve_link(v)            # /c/... -> vrai /p/ via recherche (lien propre)
    if not v.get("image"):
        _enrich_image(v)
    v["store_stock"] = _store_stock(v)
    embed = _discord_embed(v)
    button = {
        "type": 1,
        "components": [
            {"type": 2, "style": 5, "label": "🛒 Voir le deal", "url": v["url"]}
        ],
    }
    for webhook in dests:
        try:
            _http_post_json(webhook, {"embeds": [embed], "components": [button]})
        except Exception:  # noqa: BLE001
            try:
                _http_post_json(webhook, {"embeds": [embed]})
            except Exception as err:  # noqa: BLE001
                print(f"[discord] échec {webhook[-12:]}: {err}", file=sys.stderr)


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


def _slug_word(url: str) -> str:
    """Mot le plus distinctif du slug d'une fiche (pour la recherche)."""
    slug = url.rsplit("/", 1)[-1]
    words = [
        w for w in re.findall(r"[a-z]+", slug.lower())
        if len(w) > 3 and w not in _STOP_WORDS
    ]
    return max(words, key=len) if words else ""


def scan_sitemap_new(state: dict, handle) -> None:
    """Capte les fiches NOUVELLES du sitemap (éphémères/exclus hors listings).
    1er passage : mémorise tout (aucun scan). Ensuite : pour chaque nouvel ID,
    récupère prix + lien /p/ via la recherche, et déclenche `handle`."""
    if not SCAN_SITEMAP_NEW:
        return
    # Throttle : le sitemap fait ~18 Mo, on ne le retélécharge pas à chaque
    # passage (économie data proxy). Au plus toutes les SITEMAP_INTERVAL_MIN.
    nowt = time.time()
    if state.get("sitemap_ids") and nowt - float(state.get("sitemap_last", 0)) < SITEMAP_INTERVAL_MIN * 60:
        return
    state["sitemap_last"] = nowt
    seen_ids = set(state.get("sitemap_ids", []))
    cur: dict[str, str] = {}
    try:
        for sm in get_product_sitemaps():
            try:
                for loc, _ in parse_sitemap(sm):
                    m = re.search(r"-(\d+)\.html$", loc)
                    if m and "/p/" in loc:
                        cur[m.group(1)] = loc
            except Exception:  # noqa: BLE001
                continue
    except Exception as err:  # noqa: BLE001
        print(f"[sitemap] index illisible: {err}", file=sys.stderr)
        return
    if not cur:
        return
    if not seen_ids:  # 1er passage : on mémorise sans scanner
        state["sitemap_ids"] = list(cur)
        print(f"[sitemap] 1er passage : {len(cur)} fiches mémorisées (pas de scan)")
        return
    new_ids = [i for i in cur if i not in seen_ids][:SITEMAP_NEW_MAX]
    if new_ids:
        print(f"[sitemap] {len(new_ids)} nouvelle(s) fiche(s) -> vérif via recherche")

        def work(pid: str) -> list[dict]:
            q = _slug_word(cur[pid])
            if not q:
                return []
            try:
                grid = http_get(f"{_SEARCH_URL}?q={q}").decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                return []
            out: dict = {}
            _extract_tiles(grid, out)
            v = out.get(pid)
            if not v:
                return []
            v["type"] = _deal_type(v, "")
            return [v]

        with ThreadPoolExecutor(max_workers=CATEGORY_CONCURRENCY) as pool:
            for fut in as_completed([pool.submit(work, i) for i in new_ids]):
                for v in fut.result():
                    handle(v)
    # Mémorise l'état courant (les nouveaux deviennent "vus").
    state["sitemap_ids"] = list(cur)


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
            for v in tiles:
                v["type"] = _deal_type(v, slug)
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
    #    Énumération packs à CHAQUE passage (toutes les ~2 min) : fenêtre récente
    #    (nouveaux packs) + balayage tournant -> toute la plage couverte en
    #    ~25 min, en restant léger (~130 requêtes/passage). Pas de ban.
    url_candidates: set[str] = set()
    if PACK_ID_ENUM:
        floor = max(int(state.get("pack_id_max", 0)), 700)
        ids: set[int] = set()
        if PACK_ID_MAX:
            ids = set(range(1, PACK_ID_MAX + 1))
            print(f"Énumération packs: mb1..mb{PACK_ID_MAX}")
        else:
            # a) Fenêtre récente : capte vite les nouveaux packs éphémères.
            ids |= set(range(max(1, floor - PACK_ID_LOOKBACK), floor + PACK_ID_BUFFER + 1))
            # b) Balayage tournant : un bout de toute la plage, qui avance à
            #    chaque passage -> couvre TOUS les IDs au fil du temps.
            roll = int(state.get("pack_roll", 1))
            if roll < 1 or roll > floor:
                roll = 1
            ids |= set(range(roll, min(roll + PACK_ROLL_CHUNK, floor) + 1))
            nxt = roll + PACK_ROLL_CHUNK
            state["pack_roll"] = nxt if nxt <= floor else 1
            print(
                f"Énumération packs : récents mb{floor - PACK_ID_LOOKBACK}.."
                f"mb{floor + PACK_ID_BUFFER} + balayage mb{roll}.."
                f"mb{min(roll + PACK_ROLL_CHUNK, floor)}"
            )
        url_candidates |= {f"{SITE_ROOT}/mb{n}.html" for n in ids}
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
        # Concurrence faible pour ne pas réveiller l'anti-bot sur les fiches.
        with ThreadPoolExecutor(max_workers=CATEGORY_CONCURRENCY) as pool:
            for fut in as_completed([pool.submit(worker, u) for u in url_candidates]):
                done += 1
                for v in fut.result():
                    # Un pack (URL /mbN.html) -> catégorie "collectors".
                    if MB_URL_RE.search(v.get("url", "")):
                        v["type"] = "collector"
                    handle(v)
                if done % 250 == 0:
                    sd_notify("WATCHDOG=1")

    # 2bis) SITEMAP INCRÉMENTAL : capte les éphémères/exclus absents des listings
    #       (nouvelles fiches du sitemap -> prix+lien via recherche). Au passage
    #       COMPLET seulement (lourd au 1er coup, léger ensuite).
    if not extra_only:
        try:
            scan_sitemap_new(state, handle)
        except Exception as err:  # noqa: BLE001
            print(f"[sitemap] {err}", file=sys.stderr)
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
