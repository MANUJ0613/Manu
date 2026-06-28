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
TOP_ITEMS = int(os.environ.get("TOP_ITEMS", "25"))

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
    """Détecte une page-challenge anti-bot (DataDome) au lieu du JSON attendu."""
    if len(content) < 2:
        return True
    head = content[:600].lstrip().lower()
    if head[:1] in (b"{", b"["):  # JSON valide
        return False
    return b"datadome" in head or b"<html" in head or b"captcha" in head


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


def fetch_catalog(query: str) -> list[dict]:
    """Lit jusqu'à MAX_PAGES pages du catalogue pour une recherche.

    Renvoie une liste d'articles normalisés : id, title, price, brand, size,
    status (état), favourites, url, image. Les vues ne sont PAS ici (voir
    fetch_views).
    """
    items: list[dict] = []
    seen_ids: set = set()
    for page in range(1, MAX_PAGES + 1):
        params = {
            "search_text": query,
            "page": page,
            "per_page": PER_PAGE,
            "order": CATALOG_ORDER,
            "currency": CURRENCY,
        }
        if PRICE_FROM:
            params["price_from"] = PRICE_FROM
        if PRICE_TO:
            params["price_to"] = PRICE_TO
        url = f"{API_ROOT}/catalog/items?" + urllib.parse.urlencode(params)
        data = http_get_json(url)
        raw_items = data.get("items") or []
        for it in raw_items:
            iid = it.get("id")
            if iid in seen_ids:
                continue  # même article revu sur une autre page
            seen_ids.add(iid)
            items.append(
                {
                    "id": it.get("id"),
                    "title": it.get("title") or "",
                    "price": _price(it),
                    "brand": it.get("brand_title") or "",
                    "size": it.get("size_title") or "",
                    "status": it.get("status") or "",
                    "favourites": int(it.get("favourite_count") or 0),
                    "views": None,  # rempli par fetch_views (option)
                    "url": it.get("url") or f"{SITE_ROOT}/items/{it.get('id')}",
                    "image": _photo_url(it),
                    "query": query,
                }
            )
        # Stop si dernière page atteinte.
        pag = data.get("pagination") or {}
        total_pages = int(pag.get("total_pages") or 0)
        if not raw_items or (total_pages and page >= total_pages):
            break
    return items


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
# Analyse / score de demande
# --------------------------------------------------------------------------- #

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

    prices = [i["price"] for i in items]
    favs = [i["favourites"] for i in items]
    views_known = [i["views"] for i in items if i["views"] is not None]
    ranked = sorted(items, key=lambda i: i["score"], reverse=True)

    return {
        "query": query,
        "n_listings": len(items),
        "total_favourites": sum(favs),
        "avg_favourites": _mean(favs),
        "max_favourites": max(favs) if favs else 0,
        "avg_views": _mean(views_known) if views_known else None,
        "total_views": sum(views_known) if views_known else None,
        "median_price": _median(prices),
        "min_price": round(min([p for p in prices if p is not None]), 2)
        if any(p is not None for p in prices)
        else None,
        "demand_index": round(_mean([i["score"] for i in items]) or 0, 2),
        "top_items": ranked[: max(TOP_VIEWS, 10)],
        "all_items": items,
    }


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
# Main
# --------------------------------------------------------------------------- #

def run_once() -> int:
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

    print_report(results)
    write_reports(results)
    send_digest(results)
    return 0


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
