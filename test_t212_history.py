"""Rebuilding a Trading 212 net-liq curve from its primitives.

T212 exposes no account-value history. It does expose every fill, every cash
movement and every dividend — and with daily closes that is enough to compute
what the account was worth on any past day.
"""

import unittest
from datetime import date

from t212_history import (reconstruct_net_liq, yahoo_candidates,
                          yearly_transfers)


def _fill(d, sym, qty, price, wallet, ccy="USD"):
    """One fill: price in the instrument's currency, wallet in the account's."""
    return {"date": d, "symbol": sym, "quantity": qty, "is_buy": wallet < 0,
            "native_price": price, "native_currency": ccy,
            "wallet_net_value": wallet}


def _cash(d, amount, kind="DEPOSIT"):
    return {"date": d, "amount": amount, "type": kind}


# EUR/USD by day — one euro buys this many dollars.
_FX = {date(2026, 7, 1): 1.10, date(2026, 7, 2): 1.10, date(2026, 7, 3): 1.20}
_CLOSES = {"AAPL": {date(2026, 7, 1): 100.0, date(2026, 7, 2): 110.0,
                    date(2026, 7, 3): 110.0}}


class TestCashOnly(unittest.TestCase):
    def test_a_deposit_before_any_trade_is_the_whole_account(self):
        series, unpriced = reconstruct_net_liq(
            [], [_cash(date(2026, 7, 1), 1000.0)], {}, _FX,
            date(2026, 7, 1), date(2026, 7, 2))
        # EUR 1,000 at 1.10 = USD 1,100
        self.assertAlmostEqual(series[0]["close"], 1100.0)
        self.assertEqual(unpriced, [])

    def test_the_curve_converts_at_each_day_s_own_rate(self):
        """Not today's. A euro account whose currency moved would otherwise
        show a gain or loss that never happened — or hide one that did."""
        series, _ = reconstruct_net_liq(
            [], [_cash(date(2026, 7, 1), 1000.0)], {}, _FX,
            date(2026, 7, 1), date(2026, 7, 3))
        self.assertAlmostEqual(series[-1]["close"], 1200.0)

    def test_cash_movements_accumulate_from_the_day_they_land(self):
        series, _ = reconstruct_net_liq(
            [], [_cash(date(2026, 7, 1), 1000.0), _cash(date(2026, 7, 3), 500.0)],
            {}, _FX, date(2026, 7, 1), date(2026, 7, 3))
        self.assertAlmostEqual(series[0]["close"], 1100.0)
        self.assertAlmostEqual(series[-1]["close"], 1800.0)   # 1500 EUR x 1.20


class TestPositions(unittest.TestCase):
    def test_a_purchase_moves_value_from_cash_into_shares(self):
        """The day you buy, nothing should change but the shape of it."""
        fills = [_fill(date(2026, 7, 1), "AAPL", 5, 100.0, -454.55)]
        series, _ = reconstruct_net_liq(
            fills, [_cash(date(2026, 7, 1), 1000.0)], _CLOSES, _FX,
            date(2026, 7, 1), date(2026, 7, 1))
        # 545.45 EUR cash + 5 x $100 (= 454.55 EUR) -> 1,000 EUR -> $1,100
        self.assertAlmostEqual(series[0]["close"], 1100.0, places=1)

    def test_the_position_is_marked_at_each_day_s_close(self):
        fills = [_fill(date(2026, 7, 1), "AAPL", 5, 100.0, -454.55)]
        series, _ = reconstruct_net_liq(
            fills, [_cash(date(2026, 7, 1), 1000.0)], _CLOSES, _FX,
            date(2026, 7, 1), date(2026, 7, 2))
        # AAPL 100 -> 110: +$50 on the position, cash unchanged
        self.assertAlmostEqual(series[-1]["close"], 1150.0, places=1)

    def test_shares_bought_later_do_not_count_earlier(self):
        fills = [_fill(date(2026, 7, 3), "AAPL", 5, 110.0, -458.33)]
        series, _ = reconstruct_net_liq(
            fills, [_cash(date(2026, 7, 1), 1000.0)], _CLOSES, _FX,
            date(2026, 7, 1), date(2026, 7, 3))
        self.assertAlmostEqual(series[0]["close"], 1100.0)

    def test_a_sale_returns_the_proceeds_to_cash(self):
        fills = [_fill(date(2026, 7, 1), "AAPL", 5, 100.0, -454.55),
                 _fill(date(2026, 7, 2), "AAPL", 5, 110.0, 500.0)]
        series, _ = reconstruct_net_liq(
            fills, [_cash(date(2026, 7, 1), 1000.0)], _CLOSES, _FX,
            date(2026, 7, 1), date(2026, 7, 2))
        # 545.45 + 500 = 1,045.45 EUR, no shares left -> $1,150
        self.assertAlmostEqual(series[-1]["close"], 1150.0, places=1)


class TestUnpriceable(unittest.TestCase):
    def test_a_holding_with_no_closes_is_carried_at_cost_and_reported(self):
        """WEBN has no Yahoo symbol that resolves. Valuing it at zero would
        understate the account by a whole position; silently dropping it would
        be worse. Carried at what it cost, and named so the page can say so."""
        fills = [_fill(date(2026, 7, 1), "WEBN", 10, 12.0, -120.0, "EUR")]
        series, unpriced = reconstruct_net_liq(
            fills, [_cash(date(2026, 7, 1), 1000.0)], {}, _FX,
            date(2026, 7, 1), date(2026, 7, 2))
        self.assertEqual(unpriced, ["WEBN"])
        self.assertAlmostEqual(series[0]["close"], 1100.0, places=1)

    def test_a_priced_holding_is_not_reported_as_unpriced(self):
        fills = [_fill(date(2026, 7, 1), "AAPL", 5, 100.0, -454.55)]
        _, unpriced = reconstruct_net_liq(
            fills, [], _CLOSES, _FX, date(2026, 7, 1), date(2026, 7, 1))
        self.assertEqual(unpriced, [])


class TestGaps(unittest.TestCase):
    def test_a_day_with_no_close_carries_the_last_one(self):
        """Weekends and holidays. Skipping them would leave a curve with holes
        where the account very much still existed."""
        closes = {"AAPL": {date(2026, 7, 1): 100.0, date(2026, 7, 3): 130.0}}
        fills = [_fill(date(2026, 7, 1), "AAPL", 5, 100.0, -454.55)]
        series, _ = reconstruct_net_liq(
            fills, [_cash(date(2026, 7, 1), 1000.0)], closes, _FX,
            date(2026, 7, 1), date(2026, 7, 2))
        self.assertEqual(len(series), 2)
        self.assertAlmostEqual(series[1]["close"], 1100.0, places=1)

    def test_no_history_at_all_yields_an_empty_series(self):
        series, _ = reconstruct_net_liq([], [], {}, _FX,
                                        date(2026, 7, 1), date(2026, 7, 3))
        self.assertEqual(series, [])


class TestYearlyTransfers(unittest.TestCase):
    def test_deposits_net_of_withdrawals_per_year_and_month(self):
        moves = [_cash(date(2026, 7, 16), 5000.0),
                 _cash(date(2026, 8, 1), 1641.0),
                 _cash(date(2026, 8, 9), -500.0, "WITHDRAW"),
                 _cash(date(2026, 8, 9), 1.10, "INTEREST_ON_FREE_CASH")]
        out = yearly_transfers(moves, {date(2026, 7, 16): 1.10,
                                       date(2026, 8, 1): 1.10,
                                       date(2026, 8, 9): 1.10})
        self.assertAlmostEqual(out[2026]["total"], (5000 + 1641 - 500) * 1.10)
        self.assertAlmostEqual(out[2026]["months"][7], 5000 * 1.10)

    def test_interest_is_not_a_transfer(self):
        """It is a return on the account, not money you put in. Counting it
        would flatter every deposit-adjusted return."""
        out = yearly_transfers([_cash(date(2026, 8, 9), 1.10,
                                      "INTEREST_ON_FREE_CASH")],
                               {date(2026, 8, 9): 1.10})
        self.assertEqual(out, {})



class TestYahooSymbol(unittest.TestCase):
    """Translating a T212 instrument into something Yahoo answers to."""

    def test_a_us_listing_is_the_bare_symbol(self):
        self.assertEqual(yahoo_candidates("RDDT", "US", "USD"), ["RDDT"])

    def test_a_known_exchange_gets_its_suffix(self):
        """Offered, not preferred — the bare symbol still leads, see below."""
        self.assertIn("AIR.PA", yahoo_candidates("AIR", "EPA", "EUR"))

    def test_an_unknown_exchange_falls_back_to_the_currency(self):
        """The Amundi ETF's T212 code is "WEBN1d_EQ", which carries no exchange
        at all. Its listing currency is the only clue left, and a euro listing
        is far more likely on Xetra than nowhere."""
        cands = yahoo_candidates("WEBN", "", "EUR")
        self.assertEqual(cands[0], "WEBN")
        self.assertIn("WEBN.DE", cands)

    def test_the_bare_symbol_is_always_tried_first(self):
        """It is the only one that can be right for a US listing, and trying a
        suffix first would mis-resolve a name that exists on two exchanges."""
        for ex, ccy in (("", "EUR"), ("XETRA", "EUR"), ("", "")):
            self.assertEqual(yahoo_candidates("X", ex, ccy)[0], "X")

    def test_an_unknown_currency_offers_only_the_bare_symbol(self):
        """Better no candidate than a guessed suffix that silently resolves to
        a different company."""
        self.assertEqual(yahoo_candidates("X", "", "ZWL"), ["X"])


if __name__ == "__main__":
    unittest.main()
