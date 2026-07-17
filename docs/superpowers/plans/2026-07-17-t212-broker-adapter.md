# Trading 212 read-only broker adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Trading 212 as a third, read-only broker so the owner's Invest-account positions and balance appear in the app's portfolio views.

**Architecture:** New `t212_api.py` read-only REST client (HTTP Basic auth, throttled, 429-aware) that normalises T212 positions/cash into the exact `(cost_basis, account_id)` and balances shapes TT/IBKR already return. `broker_adapter.py` gains a `"t212"` branch per routed function; options-only functions return neutral empties. Credentials live in Supabase via the existing credential store; a connect form and broker switcher expose it in the UI.

**Tech Stack:** Python 3.13, `requests`, Streamlit, Supabase (`user_credentials` table), `unittest` + `unittest.mock`.

## Global Constraints

- Lint clean before every commit: `python3 -m ruff check .` (ruff.toml).
- Existing suites must stay green: `python3 -m pytest test_tastytrade_api.py test_ibkr_api.py -v` (81 tests).
- No secrets in code — credentials only via Supabase `user_credentials` / `st.secrets`.
- Read-only only — never call or expose any T212 write/order endpoint.
- Live environment only: base URL `https://live.trading212.com/api/v0`. No demo/paper env.
- Auth: `Authorization: Basic ` + base64(`API_KEY:API_SECRET`).
- T212 rate limits (per account): positions 1 req/1s; account/cash 1 req/5s; metadata cached.
- New client tests run fully offline (mocked `requests`), no network or real credentials.

---

### Task 1: T212 credential bundle in `config_store.py`

**Files:**
- Modify: `config_store.py` (after `delete_ibkr_credentials`, ~line 399)

**Interfaces:**
- Consumes: existing `save_credential`, `load_credential`, `delete_credential`.
- Produces: `T212_CREDENTIAL_KEYS`, `save_t212_credentials(client, creds)`, `load_t212_credentials(client) -> dict | None`, `delete_t212_credentials(client)`. `creds` dict keys: `t212_api_key`, `t212_api_secret`.

- [ ] **Step 1: Add the bundle helpers** (mirror the IBKR bundle exactly)

```python
# ---------------------------------------------------------------------------
# Trading 212 credential bundle
# ---------------------------------------------------------------------------

T212_CREDENTIAL_KEYS = [
    "t212_api_key",
    "t212_api_secret",
]


def save_t212_credentials(client, creds):
    """Save all T212 credentials. creds is a dict with keys matching T212_CREDENTIAL_KEYS."""
    for key in T212_CREDENTIAL_KEYS:
        if creds.get(key):
            save_credential(client, key, creds[key])


def load_t212_credentials(client):
    """Load all T212 credentials. Returns dict or None if not connected."""
    result = {}
    for key in T212_CREDENTIAL_KEYS:
        val = load_credential(client, key)
        if val:
            result[key] = val
    if "t212_api_key" in result and "t212_api_secret" in result:
        return result
    return None


def delete_t212_credentials(client):
    """Delete all T212 credentials."""
    for key in T212_CREDENTIAL_KEYS:
        try:
            delete_credential(client, key)
        except Exception:
            pass
```

- [ ] **Step 2: Lint**

Run: `python3 -m ruff check config_store.py`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add config_store.py
git commit -m "feat(t212): credential bundle helpers in config_store"
```

---

### Task 2: Auth header builder (`t212_api.py`)

**Files:**
- Create: `t212_api.py`
- Test: `test_t212_api.py`

**Interfaces:**
- Produces: `LIVE_BASE_URL: str`; `_auth_header(creds: dict) -> dict` returning `{"Authorization": "Basic <b64>"}` where b64 = base64 of `"{t212_api_key}:{t212_api_secret}"`.

- [ ] **Step 1: Write the failing test**

```python
# test_t212_api.py
"""
Unit tests for t212_api.py — the read-only Trading 212 broker client.

All HTTP is mocked; tests run without network access or real credentials.
"""

import base64
import unittest
from unittest.mock import MagicMock, patch

import t212_api


_CREDS = {"t212_api_key": "KEY123", "t212_api_secret": "SECRET456"}


class TestAuthHeader(unittest.TestCase):
    def test_auth_header_is_basic_base64_key_colon_secret(self):
        header = t212_api._auth_header(_CREDS)
        expected = base64.b64encode(b"KEY123:SECRET456").decode()
        self.assertEqual(header["Authorization"], f"Basic {expected}")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test_t212_api.py::TestAuthHeader -v`
Expected: FAIL — `ModuleNotFoundError: No module named 't212_api'`.

- [ ] **Step 3: Write minimal implementation**

```python
# t212_api.py
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


def _auth_header(creds: dict) -> dict:
    """Build the HTTP Basic auth header from key+secret."""
    raw = f"{creds['t212_api_key']}:{creds['t212_api_secret']}".encode()
    return {"Authorization": "Basic " + base64.b64encode(raw).decode()}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test_t212_api.py::TestAuthHeader -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add t212_api.py test_t212_api.py
git commit -m "feat(t212): auth header builder + test"
```

---

### Task 3: Throttled GET helper with 429 retry

**Files:**
- Modify: `t212_api.py`
- Test: `test_t212_api.py`

**Interfaces:**
- Produces: `_get(path: str, creds: dict, *, min_interval: float = 1.0, max_retries: int = 3) -> object`. Issues `GET {LIVE_BASE_URL}{path}` with the auth header. Sleeps so consecutive calls to the same `path` are ≥ `min_interval` apart. On HTTP 429, sleeps `Retry-After` seconds (default `min_interval`) and retries up to `max_retries`. Returns parsed JSON. Raises `requests.HTTPError` on non-429 error status.

- [ ] **Step 1: Write the failing tests**

```python
class TestGet(unittest.TestCase):
    def _resp(self, status=200, json_data=None, headers=None):
        r = MagicMock()
        r.status_code = status
        r.json.return_value = json_data if json_data is not None else {}
        r.headers = headers or {}
        r.raise_for_status.side_effect = (
            None if status < 400 else requests.HTTPError(f"{status}")
        )
        return r

    @patch("t212_api.time.sleep", return_value=None)
    @patch("t212_api.requests.get")
    def test_get_sends_auth_and_returns_json(self, mock_get, _sleep):
        mock_get.return_value = self._resp(200, {"ok": True})
        out = t212_api._get("/equity/positions", _CREDS)
        self.assertEqual(out, {"ok": True})
        _, kwargs = mock_get.call_args
        self.assertIn("Authorization", kwargs["headers"])

    @patch("t212_api.time.sleep", return_value=None)
    @patch("t212_api.requests.get")
    def test_get_retries_on_429_then_succeeds(self, mock_get, _sleep):
        mock_get.side_effect = [
            self._resp(429, headers={"Retry-After": "1"}),
            self._resp(200, {"ok": 1}),
        ]
        out = t212_api._get("/equity/account/cash", _CREDS, max_retries=3)
        self.assertEqual(out, {"ok": 1})
        self.assertEqual(mock_get.call_count, 2)

    @patch("t212_api.time.sleep", return_value=None)
    @patch("t212_api.requests.get")
    def test_get_raises_after_exhausting_retries(self, mock_get, _sleep):
        mock_get.return_value = self._resp(429, headers={"Retry-After": "1"})
        with self.assertRaises(requests.HTTPError):
            t212_api._get("/equity/positions", _CREDS, max_retries=2)
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest test_t212_api.py::TestGet -v`
Expected: FAIL — `_get` not defined.

- [ ] **Step 3: Implement**

```python
# add to t212_api.py

# Module-level per-path timestamp of the last request, so we honour T212's
# per-endpoint rate limits without a shared client object.
_LAST_CALL: dict = {}


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
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest test_t212_api.py::TestGet -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add t212_api.py test_t212_api.py
git commit -m "feat(t212): throttled GET helper with 429 retry"
```

---

### Task 4: Instrument-code resolution (exchange-agnostic)

**Files:**
- Modify: `t212_api.py`
- Test: `test_t212_api.py`

**Interfaces:**
- Produces: `_resolve_instruments(creds: dict) -> dict[str, dict]` mapping T212 code → `{"symbol": str, "currency": str, "isin": str, "exchange": str}`. Fetches `GET /equity/metadata/instruments` once and caches module-level. `_clean(code: str) -> dict` resolves a single code, using the metadata map and falling back to suffix-strip (`AAPL_US_EQ` → symbol `AAPL`, exchange `US`, currency `""`) when the code is absent from metadata.

Metadata item fields used: `ticker` (the code), `shortName` (clean symbol), `currencyCode`, `isin`. Exchange is the middle segment of the code (`AAPL_US_EQ` → `US`).

- [ ] **Step 1: Write the failing tests**

```python
_META = [
    {"ticker": "AAPL_US_EQ", "shortName": "AAPL", "currencyCode": "USD",
     "isin": "US0378331005"},
    {"ticker": "ASML_NL_EQ", "shortName": "ASML", "currencyCode": "EUR",
     "isin": "NL0010273215"},
]


class TestResolve(unittest.TestCase):
    def setUp(self):
        t212_api._INSTRUMENTS_CACHE = None  # reset module cache between tests

    @patch("t212_api._get")
    def test_resolves_us_and_non_us_codes(self, mock_get):
        mock_get.return_value = _META
        m = t212_api._resolve_instruments(_CREDS)
        self.assertEqual(m["AAPL_US_EQ"]["symbol"], "AAPL")
        self.assertEqual(m["AAPL_US_EQ"]["currency"], "USD")
        self.assertEqual(m["ASML_NL_EQ"]["symbol"], "ASML")
        self.assertEqual(m["ASML_NL_EQ"]["currency"], "EUR")
        self.assertEqual(m["ASML_NL_EQ"]["exchange"], "NL")

    @patch("t212_api._get")
    def test_metadata_fetched_once_and_cached(self, mock_get):
        mock_get.return_value = _META
        t212_api._resolve_instruments(_CREDS)
        t212_api._resolve_instruments(_CREDS)
        self.assertEqual(mock_get.call_count, 1)

    @patch("t212_api._get")
    def test_clean_falls_back_to_suffix_strip(self, mock_get):
        mock_get.return_value = _META
        info = t212_api._clean("TSLA_US_EQ", _CREDS)  # not in _META
        self.assertEqual(info["symbol"], "TSLA")
        self.assertEqual(info["exchange"], "US")
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest test_t212_api.py::TestResolve -v`
Expected: FAIL — `_resolve_instruments` not defined.

- [ ] **Step 3: Implement**

```python
# add to t212_api.py

_INSTRUMENTS_CACHE: dict | None = None


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
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest test_t212_api.py::TestResolve -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add t212_api.py test_t212_api.py
git commit -m "feat(t212): exchange-agnostic instrument resolution + cache"
```

---

### Task 5: `fetch_portfolio_data` (positions → cost_basis)

**Files:**
- Modify: `t212_api.py`
- Test: `test_t212_api.py`

**Interfaces:**
- Consumes: `_get`, `_clean`.
- Produces: `fetch_portfolio_data(creds: dict) -> tuple[dict, str]`. First element keyed by clean symbol; each value has keys: `total_credits, total_debits, dividends, shares_held, option_pl, equity_cost, total_pl, adjusted_cost, cost_per_share, trades, wheels, currency, exchange`. Second element is the account id from `/equity/account/info` (`id` field, str).

T212 positions fields used: `ticker`, `quantity`, `averagePrice`, `ppl`.

- [ ] **Step 1: Write the failing test**

```python
class TestPortfolio(unittest.TestCase):
    def setUp(self):
        t212_api._INSTRUMENTS_CACHE = None

    @patch("t212_api._get")
    def test_positions_normalise_to_cost_basis(self, mock_get):
        def _router(path, creds, **kw):
            if path == "/equity/metadata/instruments":
                return _META
            if path == "/equity/positions":
                return [
                    {"ticker": "AAPL_US_EQ", "quantity": 10,
                     "averagePrice": 150.0, "ppl": 200.0},
                    {"ticker": "ASML_NL_EQ", "quantity": 5,
                     "averagePrice": 600.0, "ppl": -50.0},
                ]
            if path == "/equity/account/info":
                return {"id": 42, "currencyCode": "EUR"}
            raise AssertionError(path)
        mock_get.side_effect = _router

        cb, acct = t212_api.fetch_portfolio_data(_CREDS)
        self.assertEqual(acct, "42")
        self.assertEqual(cb["AAPL"]["shares_held"], 10)
        self.assertEqual(cb["AAPL"]["cost_per_share"], 150.0)
        self.assertEqual(cb["AAPL"]["adjusted_cost"], 1500.0)
        self.assertEqual(cb["AAPL"]["total_pl"], 200.0)
        self.assertEqual(cb["AAPL"]["option_pl"], 0)
        self.assertEqual(cb["AAPL"]["trades"], [])
        self.assertEqual(cb["AAPL"]["currency"], "USD")
        self.assertEqual(cb["ASML"]["currency"], "EUR")
        self.assertEqual(cb["ASML"]["exchange"], "NL")
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest test_t212_api.py::TestPortfolio -v`
Expected: FAIL — `fetch_portfolio_data` not defined.

- [ ] **Step 3: Implement**

```python
# add to t212_api.py

def fetch_portfolio_data(creds: dict):
    """Return (cost_basis_by_symbol, account_id) from T212 positions."""
    positions = _get("/equity/positions", creds, min_interval=1.0)
    cost_basis = {}
    for pos in positions or []:
        info = _clean(pos.get("ticker", ""), creds)
        symbol = info["symbol"]
        shares = pos.get("quantity") or 0
        avg = pos.get("averagePrice") or 0.0
        pl = pos.get("ppl") or 0.0
        cost = shares * avg
        cost_basis[symbol] = {
            "total_credits": 0,
            "total_debits": 0,
            "dividends": 0,
            "shares_held": shares,
            "option_pl": 0,
            "equity_cost": cost,
            "total_pl": pl,
            "adjusted_cost": cost,
            "cost_per_share": avg,
            "trades": [],
            "wheels": [],
            "currency": info["currency"],
            "exchange": info["exchange"],
        }
    info = _get("/equity/account/info", creds, min_interval=5.0)
    account_id = str(info.get("id") or "")
    return cost_basis, account_id
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest test_t212_api.py::TestPortfolio -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add t212_api.py test_t212_api.py
git commit -m "feat(t212): fetch_portfolio_data normalises positions to cost_basis"
```

---

### Task 6: `fetch_account_balances` (cash → balances)

**Files:**
- Modify: `t212_api.py`
- Test: `test_t212_api.py`

**Interfaces:**
- Consumes: `_get`.
- Produces: `fetch_account_balances(creds: dict) -> dict` with the exact keys the app's balances consumers use: `net_liquidating_value, cash_balance, equity_buying_power, derivative_buying_power, maintenance_requirement, maintenance_excess, margin_equity, used_derivative_buying_power, reg_t_margin_requirement`.

T212 `/equity/account/cash` fields used: `total`, `free`.

- [ ] **Step 1: Write the failing test**

```python
class TestBalances(unittest.TestCase):
    @patch("t212_api._get")
    def test_cash_maps_to_balances_shape(self, mock_get):
        mock_get.return_value = {
            "free": 250.0, "total": 10250.0, "invested": 10000.0,
            "ppl": 250.0, "result": 0.0, "pieCash": 0.0, "blocked": 0.0,
        }
        b = t212_api.fetch_account_balances(_CREDS)
        self.assertEqual(b["net_liquidating_value"], 10250.0)
        self.assertEqual(b["cash_balance"], 250.0)
        self.assertEqual(b["margin_equity"], 10250.0)
        self.assertEqual(b["maintenance_requirement"], 0.0)
        # all expected keys present
        for k in ("equity_buying_power", "derivative_buying_power",
                  "maintenance_excess", "used_derivative_buying_power",
                  "reg_t_margin_requirement"):
            self.assertIn(k, b)
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest test_t212_api.py::TestBalances -v`
Expected: FAIL — `fetch_account_balances` not defined.

- [ ] **Step 3: Implement**

```python
# add to t212_api.py

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
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest test_t212_api.py::TestBalances -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add t212_api.py test_t212_api.py
git commit -m "feat(t212): fetch_account_balances maps cash to balances shape"
```

---

### Task 7: Wire `broker_adapter.py`

**Files:**
- Modify: `broker_adapter.py`
- Test: `test_t212_api.py`

**Interfaces:**
- Consumes: `t212_api.fetch_portfolio_data`, `t212_api.fetch_account_balances`; `st.session_state["t212_credentials"]`.
- Produces: adapter routes `"t212"` for `fetch_portfolio_data` / `fetch_account_balances`; every options-only function returns a neutral empty for `"t212"`; `get_active_broker()` / `has_active_broker()` recognise T212. `_get_t212_creds()` mirrors `_get_refresh_token()`'s worker-thread cache.

- [ ] **Step 1: Write the failing tests**

```python
class TestAdapterT212(unittest.TestCase):
    def _patch_active(self, broker):
        return patch("broker_adapter.get_active_broker", return_value=broker)

    @patch("broker_adapter._get_t212_creds", return_value=_CREDS)
    @patch("broker_adapter.t212_api")
    def test_portfolio_routes_to_t212(self, mock_t212, _creds):
        import broker_adapter
        mock_t212.fetch_portfolio_data.return_value = ({"AAPL": {}}, "42")
        with self._patch_active("t212"):
            out = broker_adapter.fetch_portfolio_data()
        self.assertEqual(out, ({"AAPL": {}}, "42"))
        mock_t212.fetch_portfolio_data.assert_called_once_with(_CREDS)

    def test_gap_functions_return_empty_for_t212(self):
        import broker_adapter
        with self._patch_active("t212"):
            self.assertEqual(broker_adapter.fetch_portfolio_greeks(), {})
            self.assertEqual(broker_adapter.fetch_net_liq_history(), [])
            self.assertEqual(broker_adapter.fetch_margin_requirements(), {})
            self.assertEqual(broker_adapter.fetch_option_chain("AAPL"), [])
            self.assertEqual(broker_adapter.fetch_yearly_transfers(), {})
```

Note: match each gap function's empty type to what its TT/IBKR counterpart returns on an empty account — inspect the real return (`{}` for greeks/margins/transfers, `[]` for net-liq history/option chain) and adjust the asserts + implementation to agree before finishing the task.

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest test_t212_api.py::TestAdapterT212 -v`
Expected: FAIL — `_get_t212_creds` not defined / no t212 routing.

- [ ] **Step 3: Implement — credential getter + detection**

In `broker_adapter.py`, add `import t212_api` at top, then after `_get_refresh_token` (line ~77):

```python
_T212_CREDS_CACHE: dict | None = None


def _get_t212_creds():
    """Get T212 credentials, working from main and worker threads (see _get_refresh_token)."""
    global _T212_CREDS_CACHE
    try:
        creds = st.session_state.get("t212_credentials")
        if creds:
            _T212_CREDS_CACHE = creds
            return creds
    except Exception:
        pass
    return _T212_CREDS_CACHE
```

Extend `get_active_broker()` (line 22-35): before the tastytrade default, add T212 detection:

```python
    has_tt = bool(st.session_state.get("tt_refresh_token"))
    has_ibkr = bool(st.session_state.get("ibkr_credentials"))
    has_t212 = bool(st.session_state.get("t212_credentials"))
    if has_t212 and not has_tt and not has_ibkr:
        return "t212"
    if has_ibkr and not has_tt:
        return "ibkr"
    if has_tt and not has_ibkr:
        return "tastytrade"
    return "tastytrade"
```

Extend `has_active_broker()` (line 40-43):

```python
    return bool(
        st.session_state.get("tt_refresh_token")
        or st.session_state.get("ibkr_credentials")
        or st.session_state.get("t212_credentials")
    )
```

- [ ] **Step 4: Implement — routing in each function**

In `fetch_portfolio_data` and `fetch_account_balances`, add the T212 branch first:

```python
def fetch_portfolio_data():
    if get_active_broker() == "t212":
        return t212_api.fetch_portfolio_data(_get_t212_creds())
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_portfolio_data()
    return tastytrade_api.fetch_portfolio_data(refresh_token=_get_refresh_token())


def fetch_account_balances():
    if get_active_broker() == "t212":
        return t212_api.fetch_account_balances(_get_t212_creds())
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_account_balances()
    return tastytrade_api.fetch_account_balances(refresh_token=_get_refresh_token())
```

For each options-only function (`fetch_margin_requirements`, `fetch_margin_for_position`, `fetch_net_liq_history`, `fetch_portfolio_greeks`, `fetch_greeks_and_bwd`, `fetch_beta_weighted_delta`, `fetch_yearly_transfers`, `fetch_margin_interest`, `fetch_option_chain`, `fetch_earnings_dates`), add a leading T212 guard returning the neutral empty matching its counterpart. Examples:

```python
def fetch_portfolio_greeks():
    if get_active_broker() == "t212":
        return {}
    ...

def fetch_net_liq_history(time_back="1y"):
    if get_active_broker() == "t212":
        return []
    ...

def fetch_option_chain(ticker, option_type="Put", min_dte=7, max_dte=60,
                       num_strikes=8, fallback_price=0.0):
    if get_active_broker() == "t212":
        return []
    ...
```

`fetch_earnings_dates(tickers)` returns `{}` for t212 (dict keyed by ticker in TT/IBKR). Verify each empty type against the corresponding TT/IBKR return before committing.

- [ ] **Step 5: Run tests**

Run: `python3 -m pytest test_t212_api.py -v`
Expected: PASS (all classes).

- [ ] **Step 6: Regression — existing suites + lint**

Run: `python3 -m pytest test_tastytrade_api.py test_ibkr_api.py -v && python3 -m ruff check .`
Expected: 81 pass, lint clean.

- [ ] **Step 7: Commit**

```bash
git add broker_adapter.py test_t212_api.py
git commit -m "feat(t212): route broker_adapter to t212, gap functions return empties"
```

---

### Task 8: UI — credential load, connect form, broker switcher

**Files:**
- Modify: `streamlit_app.py` — credential load (~2400), IBKR connect form block (~12412, add a sibling T212 form), broker switcher (~8665).

**Interfaces:**
- Consumes: `config_store.load_t212_credentials`, `save_t212_credentials`; `broker_adapter.get_active_broker`.
- Produces: `st.session_state["t212_credentials"]`; a "Trading 212" connect form; T212 in the switcher. No unit tests (UI layer is not unit-tested in this repo); verified by running the app.

- [ ] **Step 1: Load T212 credentials at startup**

After the IBKR credential load (`streamlit_app.py:2400`), add:

```python
        st.session_state["t212_credentials"] = load_t212_credentials(_sb_client)
```

Ensure `load_t212_credentials` (and `save_t212_credentials`) are in the `config_store` import list used at the top of `streamlit_app.py` (same import site as `load_ibkr_credentials`).

- [ ] **Step 2: Add the Trading 212 connect form**

In the "Connect your Broker" page, next to the IBKR form (`streamlit_app.py:~12412`), add a Trading 212 section:

```python
        st.markdown("#### Trading 212 (read-only)")
        st.markdown(
            "1. In the Trading 212 app: **Settings → API (Beta) → Generate API key**\n"
            "2. Choose a **read-only** key. Copy the **key** and **secret** "
            "(the secret is shown only once).\n"
            "3. Paste both below:"
        )
        with st.form("t212_creds_form"):
            _t212_key = st.text_input("API Key", type="password",
                                      placeholder="Your Trading 212 API key")
            _t212_secret = st.text_input("API Secret", type="password",
                                         placeholder="Your Trading 212 API secret")
            _t212_submitted = st.form_submit_button("Save", type="primary")

        if _t212_submitted and _t212_key and _t212_secret:
            _t212_creds = {
                "t212_api_key": _t212_key.strip(),
                "t212_api_secret": _t212_secret.strip(),
            }
            save_t212_credentials(_sb_client, _t212_creds)
            st.session_state["t212_credentials"] = _t212_creds
            log_page_view(_sb_client, "broker_connect:t212:success")
            st.success("Trading 212 connected.")
            st.rerun()
```

- [ ] **Step 3: Add T212 to the broker switcher**

Generalise the switcher (`streamlit_app.py:8665-8693`) to show whenever ≥2 brokers are connected and to include T212:

```python
    _connected = []
    if st.session_state.get("tt_refresh_token"):
        _connected.append(("Tastytrade", "tastytrade"))
    if st.session_state.get("ibkr_credentials"):
        _connected.append(("Interactive Brokers", "ibkr"))
    if st.session_state.get("t212_credentials"):
        _connected.append(("Trading 212", "t212"))

    if len(_connected) >= 2:
        _broker_options = [label for label, _ in _connected]
        _broker_keys = [key for _, key in _connected]
        _current = get_active_broker()
        _idx = _broker_keys.index(_current) if _current in _broker_keys else 0
        _selected = st.selectbox(
            "Active Broker", _broker_options, index=_idx,
            key="_broker_select", label_visibility="collapsed",
        )
        _new_broker = _broker_keys[_broker_options.index(_selected)]
        if _new_broker != _current:
            st.session_state["active_broker"] = _new_broker
            for k in ["portfolio_data", "portfolio_account", "portfolio_prices",
                       "net_liq_all", "yearly_transfers", "benchmark_returns",
                       "portfolio_fetched_at"]:
                st.session_state.pop(k, None)
            for k in [k for k in st.session_state if k.startswith("net_liq_")]:
                st.session_state.pop(k, None)
            st.rerun()
    elif len(_connected) == 1:
        st.session_state["active_broker"] = _connected[0][1]
```

Also update the broker label line just below (`_broker_label = ...`) to map `"t212"` → `"Trading 212"`.

- [ ] **Step 4: Lint**

Run: `python3 -m ruff check streamlit_app.py`
Expected: no errors.

- [ ] **Step 5: Manual verification**

Run the app locally, open "Connect your Broker", paste a read-only T212 key+secret, save. Confirm the Portfolio page shows T212 positions + balance, and that options panels render empty rather than crashing. (Use the `run` skill if a launch recipe is needed.)

- [ ] **Step 6: Commit**

```bash
git add streamlit_app.py
git commit -m "feat(t212): connect form, credential load, broker switcher"
```

---

## Self-Review

**Spec coverage:**
- Architecture (t212_api / adapter / UI) → Tasks 2-8. ✓
- Data normalisation incl. currency/exchange → Task 5. ✓
- Exchange-agnostic resolution (US + non-US, fallback) → Task 4. ✓
- Gap handling (neutral empties) → Task 7. ✓
- Credentials in Supabase → Tasks 1, 8. ✓
- Throttle + 429 → Task 3. ✓
- Tests offline → Tasks 2-7. ✓
- Non-US display-only, pricing/valuation deferred to v2 → not built here (correct); positions carry currency/exchange for the v2 hook. ✓

**Type consistency:** `creds` dict (`t212_api_key`/`t212_api_secret`) is used identically across config_store, t212_api, and broker_adapter. `_get`/`_clean`/`_resolve_instruments`/`fetch_portfolio_data`/`fetch_account_balances` names match between definition and call sites. cost_basis keys match the IBKR contract plus `currency`/`exchange`.

**Placeholder scan:** No TBD/TODO; every code step shows full code. The only deferred verifications are explicit "confirm the empty type against the real TT/IBKR return" notes in Task 7 — these are correctness checks against existing code, done during that task, not placeholders.

## Notes / risks carried from the spec
- T212 API is beta; confirm exact JSON field names (`positions`, `account/cash`, `account/info`, `metadata/instruments`) against the live API during Task 5/6 — adjust field reads if they differ.
- Deploy: merge to `main` → Streamlit Cloud redeploys (see the deploy-gap lesson).
