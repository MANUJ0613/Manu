"""Couche réseau anti-bot pour l'autobuy.

Une `Session` par site : impersonation TLS via curl_cffi (contourne DataDome /
Cloudflare / Incapsula comme le fait déjà le scanner Vinted), persistance des
cookies, en-têtes navigateur, et une cadence auto (jitter + backoff adaptatif)
pour ne pas se faire bannir.

Repli transparent sur la bibliothèque standard `urllib` si curl_cffi n'est pas
installé (mode dégradé — plus facilement détecté, mais ça tourne).
"""

from __future__ import annotations

import json
import os
import random
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

try:  # Impersonation TLS — fortement recommandé (déjà une dépendance du repo).
    from curl_cffi import requests as _cffi  # type: ignore
    _HAS_CFFI = True
except Exception:  # noqa: BLE001
    _cffi = None
    _HAS_CFFI = False

# Profil de navigateur imité (aligné sur ce que le repo utilise pour Vinted).
IMPERSONATE = os.environ.get("AUTOBUY_IMPERSONATE", "chrome124")
PROXY = os.environ.get("AUTOBUY_PROXY", "").strip()

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Marqueurs de challenge anti-bot (on ne veut pas les prendre pour du stock).
_CHALLENGE_MARKERS = (
    "datadome", "captcha-delivery", "geo.captcha", "px-captcha",
    "/cdn-cgi/challenge", "cf-chl", "incapsula", "_incapsula_",
    "are you a human", "access denied",
)


def looks_like_challenge(status: int, body: bytes) -> bool:
    """True si la réponse ressemble à une page anti-bot plutôt qu'à du contenu."""
    if status in (401, 403, 429, 503):
        return True
    head = body[:4000].decode("utf-8", "replace").lower()
    return any(m in head for m in _CHALLENGE_MARKERS)


@dataclass
class Response:
    status: int
    body: bytes
    headers: dict

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.body.decode("utf-8", "replace"))

    @property
    def challenged(self) -> bool:
        return looks_like_challenge(self.status, self.body)


@dataclass
class Session:
    """Session HTTP dédiée à un domaine, avec cadence intégrée.

    - `min_interval` : délai plancher entre 2 requêtes vers ce domaine (anti-ban).
    - backoff adaptatif : après un challenge/429, on double l'attente (borné).
    """

    name: str
    base_url: str = ""
    min_interval: float = float(os.environ.get("AUTOBUY_MIN_INTERVAL", "1.5"))
    cookies: dict = field(default_factory=dict)
    extra_headers: dict = field(default_factory=dict)

    _s: object = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _last: float = field(default=0.0, repr=False)
    _penalty: float = field(default=0.0, repr=False)

    def _client(self):
        if not _HAS_CFFI:
            return None
        if self._s is None:
            self._s = _cffi.Session(impersonate=IMPERSONATE)
        return self._s

    def _headers(self, extra: dict | None = None) -> dict:
        h = {
            "User-Agent": DEFAULT_UA,
            "Accept": "text/html,application/json,*/*",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        }
        h.update(self.extra_headers)
        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if extra:
            h.update(extra)
        return h

    def _gate(self) -> None:
        """Respecte la cadence min + le backoff, avec un petit jitter aléatoire."""
        with self._lock:
            wait = self.min_interval + self._penalty
            elapsed = time.monotonic() - self._last
            if elapsed < wait:
                time.sleep(wait - elapsed + random.uniform(0.05, 0.4))
            self._last = time.monotonic()

    def _absorb_cookies(self, resp_headers: dict) -> None:
        raw = resp_headers.get("set-cookie") or resp_headers.get("Set-Cookie")
        if not raw:
            return
        for chunk in (raw if isinstance(raw, list) else [raw]):
            first = str(chunk).split(";", 1)[0].strip()
            if "=" in first:
                k, v = first.split("=", 1)
                if k and v:
                    self.cookies[k.strip()] = v.strip()

    def _note(self, resp: Response) -> Response:
        # Ajuste le backoff : pénalité après un blocage, décroissance sinon.
        if resp.challenged:
            self._penalty = min((self._penalty or 1.0) * 2, 60.0)
        else:
            self._penalty = max(self._penalty * 0.5, 0.0)
            self._absorb_cookies(resp.headers)
        return resp

    def request(self, method: str, url: str, *, headers: dict | None = None,
                data=None, json_body=None, timeout: float = 20.0) -> Response:
        if url.startswith("/"):
            url = self.base_url.rstrip("/") + url
        self._gate()
        hdrs = self._headers(headers)
        body_bytes = None
        if json_body is not None:
            body_bytes = json.dumps(json_body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        elif isinstance(data, (dict, list)):
            body_bytes = urllib.parse.urlencode(data).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
        elif isinstance(data, str):
            body_bytes = data.encode("utf-8")
        elif isinstance(data, (bytes, bytearray)):
            body_bytes = bytes(data)

        client = self._client()
        if client is not None:
            kwargs = {"headers": hdrs, "timeout": timeout, "allow_redirects": True}
            if PROXY:
                kwargs["proxies"] = {"http": PROXY, "https": PROXY}
            if body_bytes is not None:
                kwargs["data"] = body_bytes
            r = client.request(method, url, **kwargs)
            # curl_cffi conserve les cookies dans sa propre jar ; on les recopie.
            try:
                for k, v in r.cookies.items():
                    self.cookies[k] = v
            except Exception:  # noqa: BLE001
                pass
            return self._note(Response(r.status_code, r.content, dict(r.headers)))

        # --- Repli urllib (sans impersonation) ---
        req = urllib.request.Request(url, data=body_bytes, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return self._note(Response(resp.status, resp.read(), dict(resp.headers)))
        except urllib.error.HTTPError as e:  # 4xx/5xx renvoient quand même un corps
            return self._note(Response(e.code, e.read() or b"", dict(e.headers or {})))
        except Exception:  # noqa: BLE001
            return Response(0, b"", {})

    # Raccourcis ------------------------------------------------------------
    def get(self, url: str, **kw) -> Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw) -> Response:
        return self.request("POST", url, **kw)

    def get_json(self, url: str, **kw):
        return self.get(url, **kw).json()


import urllib.parse  # noqa: E402  (utilisé dans request(); import tardif volontaire)
