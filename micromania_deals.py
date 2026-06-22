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
MIN_REFERENCE_PRICE = float(os.environ.get("MIN_REFERENCE_PRICE", "50"))  # 50 €
INITIAL_WINDOW_HOURS = float(os.environ.get("INITIAL_WINDOW_HOURS", "24"))
MAX_PRODUCTS = int(os.environ.get("MAX_PRODUCTS", "5000"))  # garde-fou par run

# Certains produits (notamment les PACKS) ont une fiche mais ne sont PAS dans
# le sitemap (URL en /...-mbNNN.html). On scanne donc aussi ces pages catégorie
# pour en extraire les URLs produits manquantes. Toujours scannées (pas de
# lastmod). Liste de slugs /c/<slug> séparés par des virgules.
EXTRA_CATEGORIES = [
    c.strip()
    for c in os.environ.get(
        "EXTRA_CATEGORIES",
        # Sources "haute priorité" scannées à chaque passage rapide :
        # packs, éditions collector, exclusivités et collectibles premium
        # (statues/figurines chères) — là où les erreurs de prix s'arrachent.
        "tous-nos-packs,jeux-video-edition-collector,"
        "exclusivites-micromania,exclusivites-premium,"
        "produits-derives-premium",
    ).split(",")
    if c.strip()
]
CATEGORY_SZ = int(os.environ.get("CATEGORY_SZ", "1000"))

# Énumération des packs par ID : /mbN.html redirige vers la fiche du pack
# (même pour des packs éphémères jamais listés dans une catégorie). On sonde
# toute la plage mb1..mbMAX pour ne rater aucun pack flash / erreur de prix.
PACK_ID_ENUM = os.environ.get("PACK_ID_ENUM", "true").lower() == "true"
PACK_ID_MAX = int(os.environ.get("PACK_ID_MAX", "0"))  # 0 = auto (max connu + buffer)
PACK_ID_BUFFER = int(os.environ.get("PACK_ID_BUFFER", "40"))
CONCURRENCY = int(os.environ.get("CONCURRENCY", "8"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "20"))
INCLUDE_USED = os.environ.get("INCLUDE_USED", "false").lower() == "true"
INCLUDE_PRECOMMANDE = os.environ.get("INCLUDE_PRECOMMANDE", "false").lower() == "true"
# Par défaut, on n'alerte que les produits réellement disponibles à l'achat
# (pas ceux affichant « Créer une alerte » / en rupture).
INCLUDE_UNAVAILABLE = os.environ.get("INCLUDE_UNAVAILABLE", "false").lower() == "true"

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
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()


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

try:
    from curl_cffi import requests as cffi  # type: ignore

    HAVE_CFFI = True
except Exception:  # noqa: BLE001
    HAVE_CFFI = False

_dd_cookies: dict[str, str] = {}
_dd_lock = threading.Lock()
_warmed = [False]
_last_warm = [0.0]
# Sous forte concurrence, un 403 ponctuel peut déclencher plein de re-warmups
# simultanés : on n'en autorise qu'un toutes les WARM_MIN_INTERVAL secondes.
WARM_MIN_INTERVAL = 30.0

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9",
}


def _warmup(force: bool = False) -> None:
    """GET la home avec impersonation pour (re)charger le cookie DataDome."""
    if not HAVE_CFFI:
        return
    with _dd_lock:
        first = not _warmed[0]
        if _warmed[0] and not force:
            return
        # Anti-rafale : si un autre thread vient déjà de re-seed, on ne refait pas.
        if force and (time.monotonic() - _last_warm[0]) < WARM_MIN_INTERVAL:
            return
        try:
            r = cffi.get(
                SITE_ROOT + "/",
                impersonate=IMPERSONATE,
                headers=_BROWSER_HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            for k, v in r.cookies.items():
                _dd_cookies[k] = v
            _warmed[0] = True
            _last_warm[0] = time.monotonic()
            # On ne logge qu'au tout premier warmup, sinon c'est trop bruyant.
            if first:
                print(f"[warmup] DataDome contourné ({len(_dd_cookies)} cookies)")
        except Exception as err:  # noqa: BLE001
            print(f"[warmup] échec: {err}", file=sys.stderr)


def http_get(url: str, retries: int = 3) -> bytes:
    """GET résistant à DataDome (curl_cffi) avec repli urllib."""
    last_err: Exception | None = None

    if HAVE_CFFI:
        if not _warmed[0]:
            _warmup()
        for attempt in range(retries):
            try:
                r = cffi.get(
                    url,
                    impersonate=IMPERSONATE,
                    headers=_BROWSER_HEADERS,
                    cookies=_dd_cookies,
                    timeout=REQUEST_TIMEOUT,
                )
                if r.status_code in (404, 410):
                    raise RuntimeError(f"GET {url}: HTTP {r.status_code}")
                if r.status_code == 403:
                    # Bloqué par DataDome : on re-seed le cookie et on retente.
                    _warmup(force=True)
                    last_err = RuntimeError("HTTP 403 (DataDome)")
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


def format_text(v: dict) -> str:
    """Version texte (Telegram / logs / fallback)."""
    pct = discount_pct(v)
    extra = " ".join(filter(None, [v.get("platform"), v.get("edition")])).strip()
    extra = f" ({extra})" if extra else ""
    return (
        f"🔥 DEAL Micromania -{pct}%\n"
        f"🏷 {v['title']}{extra}\n"
        f"💰 {_euro(v['current'])} (au lieu de {_euro(v['reference'])}) — {_cond_label(v)}\n"
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


def _send_discord(v: dict) -> None:
    embed = _discord_embed(v)
    button = {
        "type": 1,
        "components": [
            {"type": 2, "style": 5, "label": "🛒 Voir le deal", "url": v["url"]}
        ],
    }
    # Essai avec bouton lien ; repli sur embed seul si le webhook le refuse.
    try:
        _http_post_json(DISCORD_WEBHOOK_URL, {"embeds": [embed], "components": [button]})
    except Exception:  # noqa: BLE001
        _http_post_json(DISCORD_WEBHOOK_URL, {"embeds": [embed]})


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

    if DISCORD_WEBHOOK_URL:
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
    """Un scan. extra_only=True => seulement les sources haute priorité
    (packs + collectors/exclus + énumération d'IDs), sans le catalogue."""
    state = load_state()
    now = datetime.now(timezone.utc)

    # Fenêtre de scan : depuis le dernier run, sinon fenêtre initiale.
    if FULL_SCAN or force_full:
        cutoff = None
    elif state.get("last_scan"):
        try:
            cutoff = datetime.fromisoformat(state["last_scan"])
        except ValueError:
            cutoff = now - timedelta(hours=INITIAL_WINDOW_HOURS)
    else:
        cutoff = now - timedelta(hours=INITIAL_WINDOW_HOURS)

    kind = "RAPIDE (packs + collectors)" if extra_only else (
        "COMPLET (catalogue)" if cutoff is None else "incrémental"
    )
    print(f"== Micromania deals == {now.isoformat()} — passage {kind}")
    print(f"Seuil: -{int(DISCOUNT_THRESHOLD*100)}% | prix réf. >= {MIN_REFERENCE_PRICE:.0f}€")

    candidates: list[tuple[str, datetime | None]] = []

    # 1. Catalogue (sitemap) — sauté lors d'un passage rapide.
    if not extra_only:
        sitemaps = get_product_sitemaps()
        for sm in sitemaps:
            try:
                entries = parse_sitemap(sm)
            except Exception as err:  # noqa: BLE001
                print(f"[sitemap] {sm}: {err}", file=sys.stderr)
                continue
            for loc, lastmod in entries:
                if cutoff is None:
                    candidates.append((loc, lastmod))
                elif lastmod is None or lastmod >= cutoff:
                    candidates.append((loc, lastmod))

        # Les plus récemment modifiés d'abord, puis garde-fou MAX_PRODUCTS.
        candidates.sort(key=lambda t: (t[1] or now), reverse=True)
        if len(candidates) > MAX_PRODUCTS:
            print(f"Limitation à {MAX_PRODUCTS} fiches (sur {len(candidates)}).")
            candidates = candidates[:MAX_PRODUCTS]

    # Produits hors sitemap (PACKS notamment). Toujours ajoutés (pas de
    # lastmod), donc non soumis au cutoff ni au cap.
    known = {loc for loc, _ in candidates}

    def add(u: str) -> int:
        if u not in known:
            known.add(u)
            candidates.append((u, None))
            return 1
        return 0

    extra = 0
    pack_ids: list[int] = []
    # a) Pages catégorie (packs + éventuels /p/). On normalise les packs vers
    #    leur permalien stable /mbN.html.
    for slug in EXTRA_CATEGORIES:
        try:
            for u in get_category_products(slug):
                mm = PACK_SUFFIX_RE.search(u)
                if mm:
                    pid = int(re.search(r"-mb(\d+)\.html$", u).group(1))
                    pack_ids.append(pid)
                    u = f"{SITE_ROOT}/mb{pid}.html"
                extra += add(u)
        except Exception as err:  # noqa: BLE001
            print(f"[catégorie] {slug}: {err}", file=sys.stderr)

    # b) Énumération des IDs de packs (capte les packs éphémères / non listés).
    #    On sonde toujours au-delà de la "frontière" connue (plus haut ID de
    #    pack vivant déjà vu), pour attraper les nouveaux packs même non listés.
    if PACK_ID_ENUM:
        if PACK_ID_MAX:
            mx = PACK_ID_MAX
        else:
            floor = max(
                [*pack_ids, int(state.get("pack_id_max", 0)), 700]
            )
            mx = floor + PACK_ID_BUFFER
        probed = sum(add(f"{SITE_ROOT}/mb{n}.html") for n in range(1, mx + 1))
        extra += probed
        print(f"Énumération packs: mb1..mb{mx} (+{probed} à sonder)")

    if extra:
        print(f"Produits hors-sitemap: +{extra}")

    print(f"Fiches à inspecter: {len(candidates)}")
    if not candidates:
        if not extra_only:
            state["last_scan"] = now.isoformat()
            save_state(state)
        print("Rien de nouveau à scanner.")
        return 0

    # 2. Scrape concurrent + détection.
    seen: dict = state.get("seen", {})
    new_deals = 0
    processed = 0

    def worker(item: tuple[str, datetime | None]) -> list[dict]:
        url, _ = item
        try:
            return parse_product(url)
        except Exception as err:  # noqa: BLE001
            # 404/410/403 = ID de pack inexistant : attendu, on ne logge pas.
            msg = str(err)
            if not any(c in msg for c in ("HTTP 410", "HTTP 404", "HTTP 403")):
                print(f"[produit] {url}: {err}", file=sys.stderr)
            return []

    mb_seen: list[int] = []  # IDs de packs vivants vus (suivi de frontière)
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        futures = {pool.submit(worker, c): c for c in candidates}
        for fut in as_completed(futures):
            processed += 1
            for v in fut.result():
                mm = MB_URL_RE.search(v["url"])
                if mm and v["reference"] > 0:
                    mb_seen.append(int(mm.group(1)))
                if not is_deal(v):
                    continue
                # Dédup : on ré-alerte si le prix a encore baissé.
                key = v["url"] + "#" + v["condition"]
                prev = seen.get(key)
                if prev is not None and v["current"] >= float(prev):
                    continue
                send_alert(v)
                seen[key] = v["current"]
                new_deals += 1
            if processed % 250 == 0:
                print(f"  …{processed}/{len(candidates)} fiches")

    # 3. Sauvegarde de l'état (dont la frontière d'IDs de packs).
    state["seen"] = seen
    if mb_seen:
        state["pack_id_max"] = max(int(state.get("pack_id_max", 0)), max(mb_seen))
    if not extra_only:
        state["last_scan"] = now.isoformat()
    save_state(state)

    print(f"Terminé: {processed} fiches inspectées, {new_deals} alerte(s).")
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
    while True:
        start = time.monotonic()
        do_full = (start - last_full) >= full_every
        try:
            run_once(force_full=do_full, extra_only=not do_full)
            if do_full:
                last_full = start
        except Exception as err:  # noqa: BLE001 - la boucle ne doit pas mourir
            print(f"[boucle] erreur de scan: {err}", file=sys.stderr)
        if deadline is not None and time.monotonic() >= deadline:
            print("Fin de la fenêtre de boucle.")
            return 0
        time.sleep(LOOP_INTERVAL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
