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
CONCURRENCY = int(os.environ.get("CONCURRENCY", "8"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "20"))
INCLUDE_USED = os.environ.get("INCLUDE_USED", "false").lower() == "true"
INCLUDE_PRECOMMANDE = os.environ.get("INCLUDE_PRECOMMANDE", "false").lower() == "true"

STATE_FILE = os.environ.get("STATE_FILE", "state/state.json")
DEALS_LOG = os.environ.get("DEALS_LOG", "state/deals.log")
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
FULL_SCAN = os.environ.get("FULL_SCAN", "false").lower() == "true"

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

def http_get(url: str, retries: int = 3) -> bytes:
    """GET avec User-Agent, gestion gzip et quelques retries."""
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept-Encoding": "gzip",
                    "Accept-Language": "fr-FR,fr;q=0.9",
                },
            )
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw
        except Exception as err:  # noqa: BLE001 - on retente quoiqu'il arrive
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
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)


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

    title = ""
    tm = TITLE_RE.search(decoded)
    if tm:
        title = re.sub(r"\s+", " ", tm.group(1)).strip()
        title = title.split("|")[0].strip()

    variants: list[dict] = []
    for m in METRIC_RE.finditer(decoded):
        cur = float(m.group("cur"))
        ref = float(m.group("ref"))
        obj = _enclosing_object(decoded, m.start())
        cond_m = COND_RE.search(obj)
        preco_m = PRECO_RE.search(obj)
        edition_m = EDITION_RE.search(obj)
        platform_m = PLATFORM_RE.search(obj)
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
            }
        )
    return variants


def is_deal(v: dict) -> bool:
    """Le produit correspond-il aux critères d'alerte ?"""
    if not INCLUDE_USED and v["condition"].lower() not in ("new", "neuf"):
        return False
    if not INCLUDE_PRECOMMANDE and v["precommande"]:
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

def format_alert(v: dict) -> str:
    pct = discount_pct(v)
    cond = "Neuf" if v["condition"].lower() in ("new", "neuf") else v["condition"]
    extra = " ".join(filter(None, [v.get("platform"), v.get("edition")]))
    extra = f" ({extra.strip()})" if extra.strip() else ""
    return (
        f"🔥 DEAL Micromania -{pct}%\n"
        f"🏷 {v['title']}{extra}\n"
        f"💰 {v['current']:.2f}€ (au lieu de {v['reference']:.2f}€) — {cond}\n"
        f"🔗 {v['url']}"
    )


def post_json(url: str, payload: dict) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            # Discord renvoie 403 sans User-Agent explicite.
            "User-Agent": "MicromaniaDealsBot (https://github.com, 1.0)",
        },
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        resp.read()


def send_alert(message: str) -> None:
    if DRY_RUN:
        print("[DRY_RUN] " + message.replace("\n", " | "))
        return

    delivered = False
    if DISCORD_WEBHOOK_URL:
        try:
            post_json(DISCORD_WEBHOOK_URL, {"content": message})
            delivered = True
        except Exception as err:  # noqa: BLE001
            print(f"[discord] échec: {err}", file=sys.stderr)

    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            post_json(
                api,
                {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": message,
                    "disable_web_page_preview": False,
                },
            )
            delivered = True
        except Exception as err:  # noqa: BLE001
            print(f"[telegram] échec: {err}", file=sys.stderr)

    # Toujours journaliser dans un fichier (et sur stdout) en complément.
    print(message)
    print("-" * 60)
    try:
        os.makedirs(os.path.dirname(DEALS_LOG) or ".", exist_ok=True)
        with open(DEALS_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now(timezone.utc).isoformat()}\n{message}\n\n")
    except OSError:
        pass

    if not delivered and not (DISCORD_WEBHOOK_URL or TELEGRAM_BOT_TOKEN):
        # Aucun canal configuré : c'est normal, on a quand même loggé.
        pass


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> int:
    state = load_state()
    now = datetime.now(timezone.utc)

    # Fenêtre de scan : depuis le dernier run, sinon fenêtre initiale.
    if FULL_SCAN:
        cutoff = None
    elif state.get("last_scan"):
        try:
            cutoff = datetime.fromisoformat(state["last_scan"])
        except ValueError:
            cutoff = now - timedelta(hours=INITIAL_WINDOW_HOURS)
    else:
        cutoff = now - timedelta(hours=INITIAL_WINDOW_HOURS)

    print(f"== Micromania deals == {now.isoformat()}")
    print(f"Seuil: -{int(DISCOUNT_THRESHOLD*100)}% | prix réf. >= {MIN_REFERENCE_PRICE:.0f}€")
    print(f"Cutoff lastmod: {cutoff.isoformat() if cutoff else 'FULL SCAN'}")

    # 1. Collecte des URLs produits à inspecter.
    sitemaps = get_product_sitemaps()
    print(f"Sitemaps produits: {len(sitemaps)}")

    candidates: list[tuple[str, datetime | None]] = []
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

    print(f"Fiches à inspecter: {len(candidates)}")
    if not candidates:
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
            print(f"[produit] {url}: {err}", file=sys.stderr)
            return []

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        futures = {pool.submit(worker, c): c for c in candidates}
        for fut in as_completed(futures):
            processed += 1
            for v in fut.result():
                if not is_deal(v):
                    continue
                # Dédup : on ré-alerte si le prix a encore baissé.
                key = v["url"] + "#" + v["condition"]
                prev = seen.get(key)
                if prev is not None and v["current"] >= float(prev):
                    continue
                send_alert(format_alert(v))
                seen[key] = v["current"]
                new_deals += 1
            if processed % 250 == 0:
                print(f"  …{processed}/{len(candidates)} fiches")

    # 3. Sauvegarde de l'état.
    state["seen"] = seen
    state["last_scan"] = now.isoformat()
    save_state(state)

    print(f"Terminé: {processed} fiches inspectées, {new_deals} alerte(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
