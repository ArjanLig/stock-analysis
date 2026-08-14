"""Trading 212 tools on the LazyTheta MCP.

Read-only, per-user. Every tool resolves the caller's own API key from
Supabase by user_id — the MCP runs with the service-role key, so nothing
narrows the query for it.
"""

import unittest
from datetime import date
from unittest.mock import patch

import mcp_server

_CREDS = {"t212_api_key": "KEY", "t212_api_secret": "SECRET"}

_POSITIONS = {
    # No market_value key, matching what t212_api actually returns.
    "RDDT": {"shares_held": 6.0, "purchase_price": 162.49, "broker_price": 158.66,
             "equity_cost": -974.94, "total_pl": -22.98,
             "currency": "USD", "isin": "US75734B1008", "exchange": "US",
             "trades": [{"instrument_type": "Equity", "type": "Trade",
                         "action": "Buy to Open", "quantity": 6.0,
                         "price": 162.49, "net_value": -974.94,
                         "date": date(2026, 8, 10)}]},
    "WEBN": {"shares_held": 0.0, "purchase_price": 0.0, "broker_price": 0.0,
             "market_value": 0.0, "equity_cost": 0.0, "total_pl": 7.9,
             "currency": "USD", "isin": "IE0003XJA0J9", "exchange": "",
             "trades": [{"instrument_type": "Equity", "type": "Trade",
                         "action": "Buy to Open", "quantity": 10.0,
                         "price": 12.0, "net_value": -120.0,
                         "date": date(2026, 7, 17)},
                        {"instrument_type": "Equity", "type": "Trade",
                         "action": "Sell to Close", "quantity": 10.0,
                         "price": 13.0, "net_value": 130.0,
                         "date": date(2026, 8, 1)}]},
}

_BALANCES = {"net_liquidating_value": 2500.0, "cash_balance": 268.0,
             "currency": "USD", "native_currency": "EUR", "fx_rate": 1.1546}


class _Base(unittest.TestCase):
    def setUp(self):
        self.creds = patch("config_store.load_t212_credentials",
                           return_value=_CREDS)
        self.client = patch("mcp_server.get_supabase_client")
        self.creds_mock = self.creds.start()
        self.client.start()
        self.addCleanup(self.creds.stop)
        self.addCleanup(self.client.stop)


class TestCredentialScoping(_Base):
    def test_every_tool_resolves_the_caller_s_own_key(self):
        """The service-role key bypasses RLS, so a tool that forgets to pass
        user_id would read whichever row PostgREST returned first — another
        user's brokerage account."""
        with patch("t212_api.fetch_portfolio_data", return_value=(_POSITIONS, "42")):
            mcp_server._t212_positions_impl(user_id="user-b")
        self.assertEqual(self.creds_mock.call_args.kwargs.get("user_id"), "user-b")

    def test_a_user_without_trading_212_is_told_so(self):
        """Rather than a 401 from an API call made with no credentials."""
        self.creds_mock.return_value = None
        out = mcp_server._t212_positions_impl(user_id="user-c")
        self.assertIn("not connected", out.lower())


class TestPositions(_Base):
    def _run(self):
        with patch("t212_api.fetch_portfolio_data",
                   return_value=(_POSITIONS, "42")):
            return mcp_server._t212_positions_impl(user_id="user-a")

    def test_open_positions_come_back_with_cost_and_value(self):
        out = self._run()
        self.assertIn("RDDT", out)
        self.assertIn("162.49", out)

    def test_closed_names_are_left_out(self):
        """t212_api returns them so the Cost Basis page can show a closed card.
        A positions tool that listed them would report holdings you don't have."""
        self.assertNotIn("WEBN", self._run())

    def test_market_value_is_computed_not_read(self):
        """t212_api does not set market_value — the Streamlit layer computes it
        as price x shares on the way to the table. Reading the key gave every
        position a market value of zero."""
        import json
        out = json.loads(self._run())
        rddt = next(p for p in out["positions"] if p["ticker"] == "RDDT")
        self.assertAlmostEqual(rddt["market_value"], 6.0 * 158.66, places=2)

    def test_the_currency_is_stated(self):
        """Everything is converted to USD; saying so stops Claude from
        assuming the account currency."""
        self.assertIn("USD", self._run())


class TestBalance(_Base):
    def test_it_reports_the_converted_figures_and_the_rate(self):
        with patch("t212_api.fetch_account_balances", return_value=_BALANCES):
            out = mcp_server._t212_balance_impl(user_id="user-a")
        self.assertIn("2,500", out.replace("2500", "2,500"))
        self.assertIn("EUR", out)      # the account's own currency
        self.assertIn("1.15", out)     # the rate used


class TestTransactions(_Base):
    def _run(self, **kw):
        with patch("t212_api.fetch_portfolio_data",
                   return_value=(_POSITIONS, "42")):
            return mcp_server._t212_transactions_impl(user_id="user-a", **kw)

    def test_it_lists_fills_across_every_ticker(self):
        out = self._run()
        self.assertIn("RDDT", out)
        self.assertIn("WEBN", out)

    def test_one_ticker_can_be_singled_out(self):
        out = self._run(ticker="webn")
        self.assertIn("WEBN", out)
        self.assertNotIn("RDDT", out)

    def test_a_date_window_filters(self):
        out = self._run(start_date="2026-08-01")
        self.assertIn("2026-08-10", out)
        self.assertNotIn("2026-07-17", out)

    def test_fills_are_oldest_first(self):
        out = self._run(ticker="WEBN")
        self.assertLess(out.index("2026-07-17"), out.index("2026-08-01"))


if __name__ == "__main__":
    unittest.main()
