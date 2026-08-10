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


def fetch_portfolio_data(creds: dict):
    """Return (cost_basis_by_symbol, account_id) from T212 positions."""
    positions = _get("/equity/positions", creds, min_interval=1.0)
    cost_basis = {}
    for pos in positions or []:
        # /equity/positions nests the instrument and names its money fields
        # differently from the rest of the API. Reading them flat yielded a
        # blank symbol and zeros for everything except the share count.
        instrument = pos.get("instrument") or {}
        wallet = pos.get("walletImpact") or {}
        info = _clean(instrument.get("ticker", ""), creds)
        symbol = info["symbol"]
        shares = pos.get("quantity") or 0

        # Two currencies live in one position: averagePricePaid / currentPrice
        # follow the *instrument* (RDDT in USD), walletImpact follows the
        # *account* (EUR here). Per-share figures use the instrument's, because
        # that is the currency the app prices everything in — a cost basis of
        # EUR 140.76 sitting next to a Yahoo quote of USD 158.69 is a
        # comparison of two different things.
        avg = pos.get("averagePricePaid") or 0.0
        price = pos.get("currentPrice") or 0.0
        pl = (price - avg) * shares

        # equity_cost and cost_per_share are NEGATIVE by convention — cash that
        # left the account. Tastytrade builds them by summing signed trade
        # values, and the portfolio page relies on it: unrealized P/L is
        # `market_value + equity_cost`. Handing back a positive cost turned
        # that into an addition, which is how RDDT showed +212% on a losing
        # position.
        cost = -(shares * avg)

        cost_basis[symbol] = {
            "total_credits": 0,
            "total_debits": 0,
            "dividends": 0,
            "shares_held": shares,
            "option_pl": 0,
            "equity_cost": cost,
            "total_pl": pl,
            "adjusted_cost": cost,
            "cost_per_share": -avg,      # negative, same convention as above
            "trades": [],
            "wheels": [],
            # No option legs and no wheel cycles — a plain holding. The
            # portfolio page reads this to pick the buy-and-hold column set
            # instead of the wheel one, whose cost basis and days-held come
            # from trades that do not exist here.
            "buy_and_hold": True,
            "purchase_price": avg,       # positive, for display
            # T212 quotes the instrument itself, so the page doesn't have to
            # guess a Yahoo symbol. It gets that wrong for anything not listed
            # in the US: the Amundi ETF is WEBN.DE on Xetra, and a lookup on
            # the bare "WEBN" 404s — leaving the row at $0 market value and
            # handing 100% of the portfolio weight to the other position.
            "broker_price": price,
            # The currency the per-share figures above are in — the
            # instrument's. Not cosmetic: a portfolio total that adds a EUR
            # holding to a USD one without converting is simply wrong, so the
            # label has to travel with the number.
            "currency": info["currency"],
            "exchange": info["exchange"],
            # Same position expressed in the account's currency, straight from
            # T212. Kept so a multi-currency total can be struck without
            # inventing an FX rate.
            "account_currency": wallet.get("currency") or "",
            "account_cost": wallet.get("totalCost"),
            "account_value": wallet.get("currentValue"),
            "account_pl": wallet.get("unrealizedProfitLoss"),
        }
    info = _get("/equity/account/info", creds, min_interval=5.0)
    account_id = str(info.get("id") or "")
    return cost_basis, account_id


def fetch_account_balances(creds: dict) -> dict:
    """Return the app balances dict from T212 account cash."""
    cash = _get("/equity/account/cash", creds, min_interval=5.0) or {}
    total = cash.get("total") or 0.0
    free = cash.get("free") or 0.0
    return {
        "net_liquidating_value": total,
        "cash_balance": free,
        "equity_buying_power": free,
        "derivative_buying_power": free,
        "maintenance_requirement": 0.0,
        "maintenance_excess": 0.0,
        "margin_equity": total,
        "used_derivative_buying_power": 0.0,
        "reg_t_margin_requirement": 0.0,
    }
