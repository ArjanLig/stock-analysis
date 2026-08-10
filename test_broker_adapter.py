"""Tests for the multi-broker aggregation in broker_adapter."""

import types
import unittest
from unittest.mock import patch

import broker_adapter

# Outside a Streamlit script run there is no ScriptRunContext, so the real
# st.session_state is unusable. Swap the module's `st` for a stand-in holding a
# plain dict — that is the whole of what the adapter asks of it. Patched per
# test rather than installed in sys.modules, so the rest of the suite still
# gets the real streamlit.
_FAKE_ST = types.SimpleNamespace(session_state={})


def _pos(shares, avg, price):
    return {
        "shares_held": shares,
        "equity_cost": -(shares * avg),
        "cost_per_share": -avg,
        "purchase_price": avg,
        "broker_price": price,
        "total_pl": (price - avg) * shares,
        "trades": [],
        "wheels": [],
        "option_pl": 0,
        "dividends": 0,
    }


class TestFetchAll(unittest.TestCase):
    def setUp(self):
        p = patch.object(broker_adapter, "st", _FAKE_ST)
        p.start()
        self.addCleanup(p.stop)
        _FAKE_ST.session_state.clear()

    def _connect(self, *brokers):
        keys = {"tastytrade": "tt_refresh_token",
                "t212": "t212_credentials",
                "ibkr": "ibkr_credentials"}
        for b in brokers:
            broker_adapter.st.session_state[keys[b]] = {"x": 1}

    def test_only_connected_brokers_are_queried(self):
        """A disconnected broker is not called — no stale credentials, no
        spurious API error blocking the page."""
        self._connect("t212")
        with patch("t212_api.fetch_portfolio_data",
                   return_value=({"RDDT": _pos(6, 162.49, 158.66)}, "42")) as t212, \
             patch("tastytrade_api.fetch_portfolio_data") as tt:
            cb, _acct, failures = broker_adapter.fetch_all_portfolio_data()
        self.assertEqual(t212.call_count, 1)
        self.assertEqual(tt.call_count, 0)
        self.assertEqual(list(cb), ["RDDT"])
        self.assertEqual(failures, [])

    def test_positions_from_both_brokers_appear_once_each(self):
        self._connect("tastytrade", "t212")
        with patch("t212_api.fetch_portfolio_data",
                   return_value=({"RDDT": _pos(6, 162.49, 158.66)}, "42")), \
             patch("tastytrade_api.fetch_portfolio_data",
                   return_value=({"MSFT": _pos(10, 400.0, 420.0)}, "TT1")):
            cb, _acct, _failures = broker_adapter.fetch_all_portfolio_data()
        self.assertEqual(sorted(cb), ["MSFT", "RDDT"])
        self.assertEqual(cb["RDDT"]["broker"], "Trading 212")
        self.assertEqual(cb["MSFT"]["broker"], "Tastytrade")
        # The bare ticker travels in the row, because the dict key is a display
        # key that may be disambiguated — price and logo lookups need the real
        # symbol.
        self.assertEqual(cb["RDDT"]["symbol"], "RDDT")

    def test_the_same_ticker_at_two_brokers_stays_two_rows(self):
        """Mid-transfer the user holds DECK at both. Blending the cost bases
        would invent a purchase price that was never paid; two rows is what
        actually happened. Both keys are disambiguated, not just the second —
        a bare 'DECK' next to 'DECK (Trading 212)' reads as if the first row
        were broker-less."""
        self._connect("tastytrade", "t212")
        with patch("t212_api.fetch_portfolio_data",
                   return_value=({"DECK": _pos(10, 96.87, 96.82)}, "42")), \
             patch("tastytrade_api.fetch_portfolio_data",
                   return_value=({"DECK": _pos(4, 120.00, 96.82)}, "TT1")):
            cb, _, _ = broker_adapter.fetch_all_portfolio_data()
        self.assertEqual(sorted(cb), ["DECK (Tastytrade)", "DECK (Trading 212)"])
        self.assertEqual(cb["DECK (Trading 212)"]["shares_held"], 10)
        self.assertEqual(cb["DECK (Tastytrade)"]["shares_held"], 4)
        for row in cb.values():
            self.assertEqual(row["symbol"], "DECK")

    def test_a_failing_broker_is_reported_not_swallowed(self):
        """One dead broker must not silently shrink the portfolio: the other
        broker's rows still come through, and the caller is told which broker
        is missing so the total can be labelled incomplete rather than wrong."""
        self._connect("tastytrade", "t212")
        with patch("t212_api.fetch_portfolio_data",
                   side_effect=RuntimeError("429")), \
             patch("tastytrade_api.fetch_portfolio_data",
                   return_value=({"MSFT": _pos(10, 400.0, 420.0)}, "TT1")):
            cb, _acct, failures = broker_adapter.fetch_all_portfolio_data()
        self.assertEqual(list(cb), ["MSFT"])
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0][0], "Trading 212")
        self.assertIn("429", str(failures[0][1]))

    def test_the_account_id_comes_from_the_active_broker(self):
        self._connect("tastytrade", "t212")
        broker_adapter.st.session_state["active_broker"] = "t212"
        with patch("t212_api.fetch_portfolio_data",
                   return_value=({"RDDT": _pos(6, 162.49, 158.66)}, "42")), \
             patch("tastytrade_api.fetch_portfolio_data",
                   return_value=({"MSFT": _pos(10, 400.0, 420.0)}, "TT1")):
            _, acct, _ = broker_adapter.fetch_all_portfolio_data()
        self.assertEqual(acct, "42")


class TestCombinedBalances(unittest.TestCase):
    def setUp(self):
        p = patch.object(broker_adapter, "st", _FAKE_ST)
        p.start()
        self.addCleanup(p.stop)
        _FAKE_ST.session_state.clear()

    def test_net_liq_sums_across_brokers(self):
        broker_adapter.st.session_state["tt_refresh_token"] = "x"
        broker_adapter.st.session_state["t212_credentials"] = {"x": 1}
        with patch("t212_api.fetch_account_balances",
                   return_value={"net_liquidating_value": 1000.0,
                                 "cash_balance": 100.0}), \
             patch("tastytrade_api.fetch_account_balances",
                   return_value={"net_liquidating_value": 5000.0,
                                 "cash_balance": 200.0}):
            total, per_broker, failures = broker_adapter.fetch_all_net_liq()
        self.assertEqual(total, 6000.0)
        self.assertEqual(per_broker["Trading 212"], 1000.0)
        self.assertEqual(per_broker["Tastytrade"], 5000.0)
        self.assertEqual(failures, [])

    def test_a_failing_broker_leaves_the_total_flagged(self):
        broker_adapter.st.session_state["tt_refresh_token"] = "x"
        broker_adapter.st.session_state["t212_credentials"] = {"x": 1}
        with patch("t212_api.fetch_account_balances",
                   side_effect=RuntimeError("down")), \
             patch("tastytrade_api.fetch_account_balances",
                   return_value={"net_liquidating_value": 5000.0}):
            total, _per_broker, failures = broker_adapter.fetch_all_net_liq()
        self.assertEqual(total, 5000.0)
        self.assertEqual([f[0] for f in failures], ["Trading 212"])


if __name__ == "__main__":
    unittest.main()
