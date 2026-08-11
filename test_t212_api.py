"""
Unit tests for t212_api.py — the read-only Trading 212 broker client.

All HTTP is mocked; tests run without network access or real credentials.
"""

import base64
import unittest
from unittest.mock import MagicMock, patch

import requests

import broker_adapter
import gather_data
import t212_api


_CREDS = {"t212_api_key": "KEY123", "t212_api_secret": "SECRET456"}

_META = [
    {"ticker": "AAPL_US_EQ", "shortName": "AAPL", "currencyCode": "USD",
     "isin": "US0378331005"},
    {"ticker": "ASML_NL_EQ", "shortName": "ASML", "currencyCode": "EUR",
     "isin": "NL0010273215"},
    # A real T212 code that suffix-stripping cannot decode: the Amundi ETF is
    # "WEBN1d_EQ", which strips to "WEBN1d". Only the metadata carries "WEBN".
    {"ticker": "WEBN1d_EQ", "shortName": "WEBN", "currencyCode": "EUR",
     "isin": "IE0003XJA0J9"},
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

    @patch("gather_data.fetch_fx_rate", return_value=1.0)
    @patch("t212_api._get")
    def test_positions_normalise_to_cost_basis(self, mock_get, _fx):
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
        # Negative by convention — see TestSignConvention.
        self.assertEqual(cb["AAPL"]["cost_per_share"], -150.0)
        self.assertEqual(cb["AAPL"]["adjusted_cost"], -1500.0)
        self.assertEqual(cb["AAPL"]["purchase_price"], 150.0)
        self.assertEqual(cb["AAPL"]["total_pl"], 200.0)   # (170-150)x10, in USD
        self.assertEqual(cb["AAPL"]["option_pl"], 0)
        self.assertEqual(cb["AAPL"]["trades"], [])
        # Per-share figures are in USD — converted where the instrument is
        # quoted in something else (see TestUsdNormalisation); the
        # account-currency copy rides along for reconciling against T212.
        self.assertEqual(cb["AAPL"]["currency"], "USD")
        self.assertEqual(cb["AAPL"]["purchase_price"], 150.0)      # USD, not EUR
        self.assertEqual(cb["AAPL"]["account_currency"], "EUR")
        self.assertEqual(cb["AAPL"]["account_cost"], 1500.0)
        self.assertEqual(cb["AAPL"]["account_value"], 1700.0)
        self.assertEqual(cb["ASML"]["native_currency"], "EUR")
        self.assertEqual(cb["ASML"]["exchange"], "NL")


class TestBalances(unittest.TestCase):
    def setUp(self):
        gather_data._FX_CACHE.clear()

    @patch("gather_data.fetch_fx_rate", return_value=1.0)
    @patch("t212_api._get")
    def test_cash_maps_to_balances_shape(self, mock_get, _fx):
        def _router(path, creds, **kw):
            if path == "/equity/account/info":
                return {"id": 42, "currencyCode": "USD"}
            return {
                "free": 250.0, "total": 10250.0, "invested": 10000.0,
                "ppl": 250.0, "result": 0.0, "pieCash": 0.0, "blocked": 0.0,
            }
        mock_get.side_effect = _router
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

    @patch("t212_api._get")
    def test_a_euro_account_is_reported_in_dollars(self, mock_get):
        """T212 keeps a Dutch account in EUR. Adding that figure straight to a
        Tastytrade balance in USD would overstate nothing and understate the
        combined total by the whole FX difference — and the combined total is
        the number the portfolio header shows."""
        def _router(path, creds, **kw):
            if path == "/equity/account/info":
                return {"id": 42, "currencyCode": "EUR"}
            return {"free": 100.0, "total": 1000.0}
        mock_get.side_effect = _router
        with patch("gather_data.fetch_fx_rate", return_value=1.15):
            b = t212_api.fetch_account_balances(_CREDS)
        self.assertAlmostEqual(b["net_liquidating_value"], 1150.0)
        self.assertAlmostEqual(b["cash_balance"], 115.0)
        self.assertEqual(b["currency"], "USD")

    @patch("t212_api._get")
    def test_an_unknown_rate_reports_the_native_currency(self, mock_get):
        """Rather than pass off euros as dollars in a portfolio total."""
        def _router(path, creds, **kw):
            if path == "/equity/account/info":
                return {"id": 42, "currencyCode": "EUR"}
            return {"free": 100.0, "total": 1000.0}
        mock_get.side_effect = _router
        with patch("gather_data.fetch_fx_rate", return_value=None):
            b = t212_api.fetch_account_balances(_CREDS)
        self.assertAlmostEqual(b["net_liquidating_value"], 1000.0)
        self.assertEqual(b["currency"], "EUR")


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


class TestSignConvention(unittest.TestCase):
    """equity_cost / cost_per_share are negative — cash that left the account.

    The portfolio page computes unrealized P/L as `market_value + equity_cost`,
    matching how Tastytrade sums signed trade values. Returning a positive cost
    turned that subtraction into an addition: RDDT showed +$1,797 and +212.78%
    on a position that was actually down.
    """

    def setUp(self):
        t212_api._INSTRUMENTS_CACHE = None

    @patch("t212_api._get")
    def test_costs_are_negative_and_pl_adds_up(self, mock_get):
        def _router(path, creds, **kw):
            if path == "/equity/metadata/instruments":
                return _META
            if path == "/equity/positions":
                return [{"instrument": {"ticker": "AAPL_US_EQ", "name": "Apple",
                                        "isin": "US0378331005", "currency": "USD"},
                         "quantity": 10, "averagePricePaid": 150.0,
                         "currentPrice": 170.0,
                         "walletImpact": {"currency": "EUR", "totalCost": 1500.0,
                                          "currentValue": 1700.0,
                                          "unrealizedProfitLoss": 200.0}}]
            if path == "/equity/account/info":
                return {"id": 42}
            raise AssertionError(path)
        mock_get.side_effect = _router

        d = t212_api.fetch_portfolio_data(_CREDS)[0]["AAPL"]
        self.assertEqual(d["equity_cost"], -1500.0)
        self.assertEqual(d["cost_per_share"], -150.0)
        self.assertEqual(d["purchase_price"], 150.0)   # positive, for display
        self.assertTrue(d["buy_and_hold"])

        # The page's formula must now yield the real P/L, not cost + value.
        market_value = 10 * 170.0
        self.assertEqual(market_value + d["equity_cost"], 200.0)
        # ...and the return must be +13.3%, not +213%.
        self.assertAlmostEqual(
            (market_value + d["equity_cost"]) / abs(d["equity_cost"]) * 100,
            13.333, places=2)


class TestFxRate(unittest.TestCase):
    """USD conversion for a multi-currency portfolio."""

    def setUp(self):
        gather_data._FX_CACHE.clear()

    @patch("gather_data.fetch_stock_price")
    def test_usd_needs_no_lookup(self, mock_price):
        self.assertEqual(gather_data.fetch_fx_rate("USD"), 1.0)
        mock_price.assert_not_called()

    @patch("gather_data.fetch_stock_price", return_value=(1.1546, 0, 0))
    def test_eur_uses_the_pair_ticker(self, mock_price):
        self.assertAlmostEqual(gather_data.fetch_fx_rate("EUR"), 1.1546)
        mock_price.assert_called_once_with("EURUSD=X")

    @patch("gather_data.fetch_stock_price", return_value=(1.1546, 0, 0))
    def test_rate_is_cached_per_currency(self, mock_price):
        gather_data.fetch_fx_rate("EUR")
        gather_data.fetch_fx_rate("EUR")
        self.assertEqual(mock_price.call_count, 1)

    @patch("gather_data.fetch_stock_price", return_value=(0, 0, 0))
    def test_a_failed_lookup_returns_none_rather_than_a_wrong_number(self, _p):
        """Silently falling back to 1.0 would add euros to dollars as if they
        were the same unit — the caller must be able to tell it doesn't know."""
        self.assertIsNone(gather_data.fetch_fx_rate("EUR"))

    @patch("gather_data.fetch_stock_price", side_effect=RuntimeError("boom"))
    def test_an_exception_is_not_swallowed_into_a_rate(self, _p):
        self.assertIsNone(gather_data.fetch_fx_rate("EUR"))


class TestUsdNormalisation(unittest.TestCase):
    """Positions quoted in another currency are converted, not relabelled."""

    def setUp(self):
        t212_api._INSTRUMENTS_CACHE = None
        gather_data._FX_CACHE.clear()

    def _fetch(self):
        def _router(path, creds, **kw):
            if path == "/equity/metadata/instruments":
                return _META
            if path == "/equity/positions":
                return [
                    {"instrument": {"ticker": "AAPL_US_EQ", "name": "Apple",
                                    "isin": "US0378331005", "currency": "USD"},
                     "quantity": 10, "averagePricePaid": 150.0, "currentPrice": 170.0,
                     "walletImpact": {"currency": "EUR", "totalCost": 1300.0,
                                      "currentValue": 1473.0,
                                      "unrealizedProfitLoss": 173.0}},
                    {"instrument": {"ticker": "ASML_NL_EQ", "name": "ASML",
                                    "isin": "NL0010273215", "currency": "EUR"},
                     "quantity": 5, "averagePricePaid": 600.0, "currentPrice": 620.0,
                     "walletImpact": {"currency": "EUR", "totalCost": 3000.0,
                                      "currentValue": 3100.0,
                                      "unrealizedProfitLoss": 100.0}},
                ]
            if path == "/equity/account/info":
                return {"id": 42}
            raise AssertionError(path)
        with patch("t212_api._get", side_effect=_router), \
             patch("gather_data.fetch_stock_price", return_value=(1.20, 0, 0)):
            return t212_api.fetch_portfolio_data(_CREDS)[0]

    def test_a_usd_instrument_is_untouched(self):
        d = self._fetch()["AAPL"]
        self.assertEqual(d["purchase_price"], 150.0)
        self.assertEqual(d["broker_price"], 170.0)
        self.assertEqual(d["equity_cost"], -1500.0)
        self.assertEqual(d["currency"], "USD")

    def test_a_eur_instrument_is_converted_at_the_rate(self):
        """EUR 600 at 1.20 is USD 720 — the number changes, not just the sign
        in front of it. Leaving it at 600 under a $ label understates the
        holding and skews every weight in the table."""
        d = self._fetch()["ASML"]
        self.assertAlmostEqual(d["purchase_price"], 720.0)
        self.assertAlmostEqual(d["broker_price"], 744.0)
        self.assertAlmostEqual(d["equity_cost"], -3600.0)
        self.assertEqual(d["currency"], "USD")
        # The original is preserved so the row can say where it came from.
        self.assertEqual(d["native_currency"], "EUR")
        self.assertAlmostEqual(d["native_purchase_price"], 600.0)

    def test_an_unknown_rate_leaves_the_position_in_its_own_currency(self):
        """Better a row that says EUR than a dollar figure that is really
        euros."""
        with patch("gather_data.fetch_fx_rate", return_value=None):
            def _router(path, creds, **kw):
                if path == "/equity/metadata/instruments":
                    return _META
                if path == "/equity/positions":
                    return [{"instrument": {"ticker": "ASML_NL_EQ", "name": "ASML",
                                            "isin": "NL0010273215", "currency": "EUR"},
                             "quantity": 5, "averagePricePaid": 600.0,
                             "currentPrice": 620.0,
                             "walletImpact": {"currency": "EUR", "totalCost": 3000.0,
                                              "currentValue": 3100.0,
                                              "unrealizedProfitLoss": 100.0}}]
                if path == "/equity/account/info":
                    return {"id": 42}
                raise AssertionError(path)
            with patch("t212_api._get", side_effect=_router):
                d = t212_api.fetch_portfolio_data(_CREDS)[0]["ASML"]
        self.assertEqual(d["currency"], "EUR")
        self.assertAlmostEqual(d["purchase_price"], 600.0)


_ORDERS = {
    "items": [
        {"order": {"ticker": "WEBN1d_EQ", "side": "BUY",
                   "instrument": {"ticker": "WEBN1d_EQ", "name": "Amundi",
                                  "isin": "IE0003XJA0J9", "currency": "EUR"}},
         "fill": {"quantity": 10.0, "price": 12.00,
                  "filledAt": "2026-05-04T09:12:31.000+02:00",
                  "walletImpact": {"currency": "EUR", "netValue": -120.0}}},
        {"order": {"ticker": "WEBN1d_EQ", "side": "BUY",
                   "instrument": {"ticker": "WEBN1d_EQ", "name": "Amundi",
                                  "isin": "IE0003XJA0J9", "currency": "EUR"}},
         "fill": {"quantity": 13.73, "price": 13.00,
                  "filledAt": "2026-06-18T11:02:00.000+02:00",
                  "walletImpact": {"currency": "EUR", "netValue": -178.49}}},
        {"order": {"ticker": "AAPL_US_EQ", "side": "SELL",
                   "instrument": {"ticker": "AAPL_US_EQ", "name": "Apple",
                                  "isin": "US0378331005", "currency": "USD"}},
         "fill": {"quantity": 2.0, "price": 200.0,
                  "filledAt": "2026-07-01T15:30:00.000+02:00",
                  "walletImpact": {"currency": "EUR", "netValue": 346.0}}},
    ],
    "nextPagePath": None,
}


class TestTrades(unittest.TestCase):
    """T212 fills become the same trade shape Tastytrade produces.

    Without them a T212 position has an average price and no history, so it
    cannot be measured against the index, given a FIFO basis, or told apart
    from a single purchase — and WEBN was bought twice.
    """

    def setUp(self):
        t212_api._INSTRUMENTS_CACHE = None
        gather_data._FX_CACHE.clear()

    def _trades(self, rate=1.0):
        def _router(path, creds, **kw):
            if path == "/equity/metadata/instruments":
                return _META
            if path.startswith("/equity/history/orders"):
                return _ORDERS
            raise AssertionError(path)
        with patch("t212_api._get", side_effect=_router), \
             patch("gather_data.fetch_fx_rate", return_value=rate):
            return t212_api.fetch_trades(_CREDS)

    def test_each_fill_becomes_a_dated_lot(self):
        webn = self._trades()["WEBN"]
        self.assertEqual([t["quantity"] for t in webn], [10.0, 13.73])
        self.assertEqual([t["date"].isoformat() for t in webn],
                         ["2026-05-04", "2026-06-18"])
        self.assertEqual([t["instrument_type"] for t in webn], ["Equity"] * 2)

    def test_a_buy_costs_cash_and_a_sale_returns_it(self):
        """net_value carries the app's sign convention, since every downstream
        walk reads the sign to tell a purchase from a sale."""
        webn = self._trades()["WEBN"]
        self.assertLess(webn[0]["net_value"], 0)
        self.assertIn("Buy", webn[0]["action"])
        aapl = self._trades()["AAPL"][0]
        self.assertGreater(aapl["net_value"], 0)
        self.assertIn("Sell", aapl["action"])

    def test_prices_are_converted_like_the_positions_are(self):
        """A EUR fill next to a USD cost basis would misplace the lot by the
        whole FX difference."""
        webn = self._trades(rate=1.15)["WEBN"]
        self.assertAlmostEqual(webn[0]["price"], 13.80)
        self.assertAlmostEqual(webn[0]["net_value"], -138.00)

    def test_trades_arrive_oldest_first(self):
        """FIFO retires the oldest lot, so the order the broker returns them in
        cannot be trusted to be the order they happened in."""
        webn = self._trades()["WEBN"]
        self.assertEqual(webn, sorted(webn, key=lambda t: t["date"]))

    def test_pagination_follows_the_next_page(self):
        seen = []

        def _router(path, creds, **kw):
            if path == "/equity/metadata/instruments":
                return _META
            seen.append(path)
            if len(seen) == 1:
                return {"items": _ORDERS["items"][:1],
                        "nextPagePath": "/equity/history/orders?cursor=2"}
            return {"items": _ORDERS["items"][1:], "nextPagePath": None}

        with patch("t212_api._get", side_effect=_router), \
             patch("gather_data.fetch_fx_rate", return_value=1.0):
            out = t212_api.fetch_trades(_CREDS)
        self.assertEqual(len(seen), 2)
        self.assertEqual(len(out["WEBN"]), 2)

    def test_an_unreachable_history_yields_nothing_rather_than_failing(self):
        """The positions themselves still render; they just keep the broker's
        average price and no purchase date."""
        def _router(path, creds, **kw):
            if path == "/equity/metadata/instruments":
                return _META
            raise RuntimeError("429")
        with patch("t212_api._get", side_effect=_router):
            self.assertEqual(t212_api.fetch_trades(_CREDS), {})
