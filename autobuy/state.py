"""État persistant : dédup (achat/alerte unique) + plafond de dépense quotidien.

Stocké dans `state/autobuy_state.json` (surchargé par AUTOBUY_STATE_FILE). Fichier
ignoré par git (peut contenir l'historique de tes achats).
"""

from __future__ import annotations

import json
import os
import threading
import time

STATE_FILE = os.environ.get("AUTOBUY_STATE_FILE", "state/autobuy_state.json")
DAILY_SPEND_CAP = float(os.environ.get("AUTOBUY_DAILY_SPEND_CAP", "300"))  # €/jour

_lock = threading.Lock()
_data = {
    "bought": {},        # key -> {ts, price, label}
    "alerted": {},       # key -> ts (anti-spam d'alerte restock)
    "spend": {},         # "YYYY-MM-DD" -> total dépensé (achats tentés)
}
_loaded = False

# Ré-alerte au plus une fois toutes N secondes pour un même produit encore en stock.
ALERT_COOLDOWN = float(os.environ.get("AUTOBUY_ALERT_COOLDOWN", "1800"))


def _day(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts if ts is not None else time.time()))


def load() -> None:
    global _loaded
    with _lock:
        if _loaded:
            return
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, encoding="utf-8") as fh:
                    disk = json.load(fh)
                for k in ("bought", "alerted", "spend"):
                    if isinstance(disk.get(k), dict):
                        _data[k] = disk[k]
            except (OSError, json.JSONDecodeError) as err:
                print(f"[state] lecture impossible ({err}) — on repart à vide")
        _loaded = True


def _save() -> None:
    try:
        os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(_data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_FILE)
    except OSError as err:
        print(f"[state] écriture impossible: {err}")


def already_bought(key: str) -> bool:
    load()
    with _lock:
        return key in _data["bought"]


def should_alert(key: str) -> bool:
    """True si on n'a pas déjà alerté récemment pour ce produit (anti-spam)."""
    load()
    with _lock:
        last = _data["alerted"].get(key, 0)
        if time.time() - last < ALERT_COOLDOWN:
            return False
        _data["alerted"][key] = time.time()
        _save()
        return True


def spent_today() -> float:
    load()
    with _lock:
        return float(_data["spend"].get(_day(), 0.0))


def can_spend(price: float) -> bool:
    """Garde-fou : reste-t-il de l'enveloppe quotidienne pour cet achat ?"""
    try:
        price = float(price)
    except (TypeError, ValueError):
        return False
    return (spent_today() + price) <= DAILY_SPEND_CAP


def record_buy(key: str, price: float, label: str = "") -> None:
    load()
    with _lock:
        _data["bought"][key] = {"ts": time.time(), "price": price, "label": label}
        d = _day()
        _data["spend"][d] = float(_data["spend"].get(d, 0.0)) + float(price or 0)
        _save()
