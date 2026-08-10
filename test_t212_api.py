"""
Unit tests for t212_api.py — the read-only Trading 212 broker client.

All HTTP is mocked; tests run without network access or real credentials.
"""

import base64
import unittest
from unittest.mock import MagicMock, patch

import requests

import broker_adapter
import t212_api


_CREDS = {"t212_api_key": "KEY123", "t212_api_secret": "SECRET456"}

_META = [
    {"ticker": "AAPL_US_EQ", "shortName": "AAPL", "currencyCode": "USD",
     "isin": "US0378331005"},
    {"ticker": "ASML_NL_EQ", "shortName": "ASML", "currencyCode": "EUR",
     "isin": "NL0010273215"},
]


class TestAuthHeader(unittest.TestCase):
    def test_auth_header_is_basic_base64_key_colon_secret(self):
        header = t212_api._auth_header(_CREDS)
        expected = base64.b64encode(b"KEY123:SECRET456").decode()
        self.assertEqual(header["Authorization"], f"Basic {expected}")


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


class TestPortfolio(unittest.TestCase):
    def setUp(self):
        t212_api._INSTRUMENTS_CACHE = None

    @patch("t212_api._get")
    def test_positions_normalise_to_cost_basis(self, mock_get):
        def _router(path, creds, **kw):
            if path == "/equity/metadata/instruments":
                return _META
            if path == "/equity/positions":
                # Shape copied verbatim from a live /equity/positions response
                # (2026-08-10). The instrument is NESTED and the money fields
                # are named differently from what the old fixture assumed —
                # that mismatch is why every T212 row rendered as $0.00 with a
                # blank ticker while the share count came through fine.
                return [
                    {"instrument": {"ticker": "AAPL_US_EQ", "name": "Apple",
                                    "isin": "US0378331005", "currency": "USD"},
                     "quantity": 10, "averagePricePaid": 150.0,
                     "currentPrice": 170.0,
                     "walletImpact": {"currency": "EUR", "totalCost": 1500.0,
                                      "currentValue": 1700.0,
                                      "unrealizedProfitLoss": 200.0}},
                    {"instrument": {"ticker": "ASML_NL_EQ", "name": "ASML",
                                    "isin": "NL0010273215", "currency": "EUR"},
                     "quantity": 5, "averagePricePaid": 600.0,
                     "currentPrice": 590.0,
                     "walletImpact": {"currency": "EUR", "totalCost": 3000.0,
                                      "currentValue": 2950.0,
                                      "unrealizedProfitLoss": -50.0}},
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
        self.assertEqual(cb["AAPL"]["total_pl"], 200.0)   # (170-150)x10, in USD
        self.assertEqual(cb["AAPL"]["option_pl"], 0)
        self.assertEqual(cb["AAPL"]["trades"], [])
        # Per-share figures follow the instrument's currency so they line up
        # with the quote the app shows; the account-currency copy rides along
        # for striking a multi-currency total.
        self.assertEqual(cb["AAPL"]["currency"], "USD")
        self.assertEqual(cb["AAPL"]["cost_per_share"], 150.0)      # USD, not EUR
        self.assertEqual(cb["AAPL"]["account_currency"], "EUR")
        self.assertEqual(cb["AAPL"]["account_cost"], 1500.0)
        self.assertEqual(cb["AAPL"]["account_value"], 1700.0)
        self.assertEqual(cb["ASML"]["currency"], "EUR")
        self.assertEqual(cb["ASML"]["exchange"], "NL")


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


class TestAdapterT212(unittest.TestCase):
    """broker_adapter routing to t212_api + neutral empties for options-only gaps.

    Empty-return shapes below are verified against the real TT/IBKR
    implementations (not just the brief's illustrative examples):
      - fetch_margin_requirements: {} (both TT and IBKR return {} when empty)
      - fetch_margin_for_position: None (both TT docstring and IBKR return None)
      - fetch_net_liq_history: [] (both return a list of {time, close} dicts)
      - fetch_portfolio_greeks: {"positions": [], "totals": {delta/theta/gamma/vega: 0}}
        — NOT bare {}; both TT and IBKR always return this structured dict.
      - fetch_beta_weighted_delta: {"positions": [], "portfolio_bwd": 0,
        "spy_price": 0, "dollar_per_1pct": 0} — NOT bare {}, same reasoning.
      - fetch_greeks_and_bwd: tuple of the two empties above (TT/IBKR both
        return a (greeks, bwd) tuple, never a dict).
      - fetch_yearly_transfers: {} (both return a plain dict keyed by year)
      - fetch_margin_interest: {"current_month": 0, "ytd": 0, "total": 0,
        "monthly": {}} — NOT bare {}; both TT and IBKR always return this shape.
      - fetch_option_chain: {"underlying_price": fallback_price, "expirations": []}
        — NOT [], both TT and IBKR return this dict shape on empty/error.
      - fetch_earnings_dates: {ticker: None for ticker in tickers} — NOT bare
        {}; both TT and IBKR return a dict keyed by every requested ticker.
    """

    def _patch_active(self, broker):
        return patch("broker_adapter.get_active_broker", return_value=broker)

    @patch("broker_adapter._get_t212_creds", return_value=_CREDS)
    @patch("broker_adapter.t212_api")
    def test_portfolio_routes_to_t212(self, mock_t212, _creds):
        mock_t212.fetch_portfolio_data.return_value = ({"AAPL": {}}, "42")
        with self._patch_active("t212"):
            out = broker_adapter.fetch_portfolio_data()
        self.assertEqual(out, ({"AAPL": {}}, "42"))
        mock_t212.fetch_portfolio_data.assert_called_once_with(_CREDS)

    @patch("broker_adapter._get_t212_creds", return_value=_CREDS)
    @patch("broker_adapter.t212_api")
    def test_balances_routes_to_t212(self, mock_t212, _creds):
        mock_t212.fetch_account_balances.return_value = {"net_liquidating_value": 1.0}
        with self._patch_active("t212"):
            out = broker_adapter.fetch_account_balances()
        self.assertEqual(out, {"net_liquidating_value": 1.0})
        mock_t212.fetch_account_balances.assert_called_once_with(_CREDS)

    def test_gap_functions_return_empty_for_t212(self):
        with self._patch_active("t212"):
            self.assertEqual(broker_adapter.fetch_margin_requirements(), {})
            self.assertIsNone(broker_adapter.fetch_margin_for_position("AAPL", 1))
            self.assertEqual(broker_adapter.fetch_net_liq_history(), [])
            self.assertEqual(
                broker_adapter.fetch_portfolio_greeks(),
                {"positions": [], "totals": {"delta": 0, "theta": 0, "gamma": 0, "vega": 0}},
            )
            self.assertEqual(
                broker_adapter.fetch_beta_weighted_delta(),
                {"positions": [], "portfolio_bwd": 0, "spy_price": 0, "dollar_per_1pct": 0},
            )
            greeks, bwd = broker_adapter.fetch_greeks_and_bwd()
            self.assertEqual(
                greeks,
                {"positions": [], "totals": {"delta": 0, "theta": 0, "gamma": 0, "vega": 0}},
            )
            self.assertEqual(
                bwd,
                {"positions": [], "portfolio_bwd": 0, "spy_price": 0, "dollar_per_1pct": 0},
            )
            self.assertEqual(broker_adapter.fetch_yearly_transfers(), {})
            self.assertEqual(
                broker_adapter.fetch_margin_interest(),
                {"current_month": 0, "ytd": 0, "total": 0, "monthly": {}},
            )
            self.assertEqual(
                broker_adapter.fetch_option_chain("AAPL"),
                {"underlying_price": 0.0, "expirations": []},
            )
            self.assertEqual(
                broker_adapter.fetch_option_chain("AAPL", fallback_price=123.0),
                {"underlying_price": 123.0, "expirations": []},
            )
            self.assertEqual(
                broker_adapter.fetch_earnings_dates(["AAPL", "MSFT"]),
                {"AAPL": None, "MSFT": None},
            )

    def test_get_active_broker_detects_t212_alone(self):
        with patch.object(
            broker_adapter.st, "session_state",
            {"t212_credentials": _CREDS},
        ):
            self.assertEqual(broker_adapter.get_active_broker(), "t212")

    def test_has_active_broker_true_for_t212_only(self):
        with patch.object(
            broker_adapter.st, "session_state",
            {"t212_credentials": _CREDS},
        ):
            self.assertTrue(broker_adapter.has_active_broker())
