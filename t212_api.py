"""
Trading 212 read-only broker client.

Fetches Invest-account positions and cash from the Trading 212 Public API
(beta) and normalises them into the app's portfolio contract. Read-only:
no order/write endpoints are called. Live environment only.
"""

import base64
import logging
import time

import requests

logger = logging.getLogger(__name__)

LIVE_BASE_URL = "https://live.trading212.com/api/v0"

# Module-level per-path timestamp of the last request, so we honour T212's
# per-endpoint rate limits without a shared client object.
_LAST_CALL: dict = {}

# Module-level cache of instrument metadata (code -> resolved info).
_INSTRUMENTS_CACHE: dict | None = None


def _get(path: str, creds: dict, *, min_interval: float = 1.0, max_retries: int = 3):
    """GET a T212 endpoint with throttling + 429 retry. Returns parsed JSON."""
    url = f"{LIVE_BASE_URL}{path}"
    headers = _auth_header(creds)
    for attempt in range(max_retries):
        wait = min_interval - (time.time() - _LAST_CALL.get(path, 0.0))
        if wait > 0:
            time.sleep(wait)
        resp = requests.get(url, headers=headers, timeout=30)
        _LAST_CALL[path] = time.time()
        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", min_interval))
            logger.debug("T212 429 on %s; retry in %ss", path, retry_after)
            time.sleep(retry_after)
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()
    return resp.json()


def _auth_header(creds: dict) -> dict:
    """Build the HTTP Basic auth header from key+secret."""
    raw = f"{creds['t212_api_key']}:{creds['t212_api_secret']}".encode()
    return {"Authorization": "Basic " + base64.b64encode(raw).decode()}


def _suffix_strip(code: str) -> dict:
    """Fallback: derive symbol + exchange from a T212 code like AAPL_US_EQ."""
    parts = code.split("_")
    symbol = parts[0] if parts else code
    exchange = parts[1] if len(parts) > 2 else ""
    return {"symbol": symbol, "currency": "", "isin": "", "exchange": exchange}


def _resolve_instruments(creds: dict) -> dict:
    """Fetch + cache the T212 instrument metadata as code -> resolved dict."""
    global _INSTRUMENTS_CACHE
    if _INSTRUMENTS_CACHE is not None:
        return _INSTRUMENTS_CACHE
    out = {}
    for item in _get("/equity/metadata/instruments", creds, min_interval=5.0):
        code = item.get("ticker")
        if not code:
            continue
        fb = _suffix_strip(code)
        out[code] = {
            "symbol": item.get("shortName") or fb["symbol"],
            "currency": item.get("currencyCode") or "",
            "isin": item.get("isin") or "",
            "exchange": fb["exchange"],
        }
    _INSTRUMENTS_CACHE = out
    return out


def _clean(code: str, creds: dict) -> dict:
    """Resolve one instrument code, falling back to suffix-strip if unknown."""
    return _resolve_instruments(creds).get(code) or _suffix_strip(code)
