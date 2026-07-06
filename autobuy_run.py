#!/usr/bin/env python3
"""Service autobuy — sniper d'éditions collector/limitées (Micromania, Fnac, Funko).

Tourne en continu (systemd sur ton VPS). À chaque tick il vérifie la watchlist
(rapide) et, moins souvent, scanne les rayons collector. Sur un produit dispo :
alerte instantanée (ntfy + Discord) et, si tu l'as marqué "auto", ajout panier +
checkout jusqu'au 3-D Secure (que tu valides dans ton appli banque).

⚠️ DRY_RUN est activé par défaut : aucune commande réelle tant que tu ne mets pas
AUTOBUY_DRY_RUN=false. Voir deploy/autobuy.env.example.

Lancement :  python3 autobuy_run.py
Config     :  variables d'environnement (voir README section Autobuy).
"""

from __future__ import annotations

import os
import socket
import sys
import time

from autobuy import buy, monitor

WATCHLIST_INTERVAL = float(os.environ.get("AUTOBUY_WATCHLIST_INTERVAL", "10"))   # s
COLLECTOR_INTERVAL = float(os.environ.get("AUTOBUY_COLLECTOR_INTERVAL", "300"))  # s
RELOAD_INTERVAL = float(os.environ.get("AUTOBUY_RELOAD_INTERVAL", "120"))        # s
ENABLED = os.environ.get("AUTOBUY_ENABLED", "true").strip().lower() != "false"


def _sd_notify(msg: str) -> None:
    """Heartbeat systemd (Type=notify + WatchdogSec). No-op hors systemd."""
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        if addr.startswith("@"):
            addr = "\0" + addr[1:]
        s.sendto(msg.encode("utf-8"), addr)
        s.close()
    except OSError:
        pass


def main() -> int:
    if not ENABLED:
        print("AUTOBUY_ENABLED=false — service en veille.")
        return 0

    targets = monitor.load_targets()
    print(f"🛒 Autobuy démarré — {monitor.summary(targets)}")
    if buy.DRY_RUN:
        print("🧪 DRY_RUN actif : aucune commande réelle ne sera passée.")
    _sd_notify("READY=1")

    last_collector = 0.0
    last_reload = time.monotonic()

    while True:
        _sd_notify("WATCHDOG=1")
        try:
            hits = monitor.check_watchlist(targets)
            now = time.monotonic()
            if now - last_collector >= COLLECTOR_INTERVAL:
                monitor.scan_collectors()
                last_collector = now
            if now - last_reload >= RELOAD_INTERVAL:
                targets = monitor.load_targets()  # recharge la watchlist à chaud
                last_reload = now
            if hits:
                print(f"[tick] {hits} produit(s) en stock traité(s).")
        except Exception as err:  # noqa: BLE001
            print(f"[tick] erreur: {err}", file=sys.stderr)
        time.sleep(WATCHLIST_INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
