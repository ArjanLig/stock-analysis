"""Tests for the deployment / dry-powder figures on the portfolio page."""

import unittest
from datetime import date

from portfolio_metrics import (compute_deployment, display_basis,
                               held_share_cost, has_option_legs,
                               fifo_realized, open_lots, relative_performance)


def _pos(mv, symbol=None):
    return {"market_value": mv, "symbol": symbol}


class TestDeployed(unittest.TestCase):
    def test_deployed_is_market_value_over_net_liq(self):
        d = compute_deployment({"A": _pos(80.0)}, net_liq=100.0, cash=20.0,
                               target_pct=5.0)
        self.assertAlmostEqual(d["invested"], 80.0)
        self.assertAlmostEqual(d["deployed_pct"], 80.0)
        self.assertAlmostEqual(d["dry_powder"], 20.0)
        self.assertAlmostEqual(d["dry_powder_pct"], 20.0)

    def test_dry_powder_is_cash_not_the_remainder(self):
        """Net liq minus market value is not cash — at a margin broker it also
        carries the short options' value and any borrowing. Reporting the
        remainder as "dry powder" would promise money that isn't there to
        spend."""
        d = compute_deployment({"A": _pos(80.0)}, net_liq=100.0, cash=5.0,
                               target_pct=5.0)
        self.assertAlmostEqual(d["dry_powder"], 5.0)
        self.assertAlmostEqual(d["deployed_pct"], 80.0)

    def test_an_empty_portfolio_does_not_divide_by_zero(self):
        d = compute_deployment({}, net_liq=0.0, cash=0.0, target_pct=5.0)
        self.assertEqual(d["deployed_pct"], 0.0)
        self.assertEqual(d["full_count"], 0)
        self.assertEqual(d["partial"], [])


class TestFullPositions(unittest.TestCase):
    def setUp(self):
        # target 5% of 10,000 = 500 per full position; the fill band is 90%,
        # so anything from 450 up counts as full.
        self.net_liq = 10_000.0

    def test_a_position_at_target_is_full(self):
        d = compute_deployment({"A": _pos(500.0)}, self.net_liq, 0.0, 5.0)
        self.assertEqual(d["full_count"], 1)
        self.assertEqual(d["partial"], [])

    def test_the_band_keeps_a_near_miss_out_of_the_to_do_list(self):
        """4.7% of the portfolio is a full position in every sense that
        matters; flagging it as needing $15 more would bury the two names that
        genuinely have room."""
        d = compute_deployment({"A": _pos(470.0)}, self.net_liq, 0.0, 5.0)
        self.assertEqual(d["full_count"], 1)
        self.assertEqual(d["partial"], [])

    def test_a_half_position_is_partial_with_the_gap_to_target(self):
        d = compute_deployment({"A": _pos(250.0)}, self.net_liq, 0.0, 5.0)
        self.assertEqual(d["full_count"], 0)
        self.assertEqual(len(d["partial"]), 1)
        self.assertEqual(d["partial"][0]["ticker"], "A")
        self.assertAlmostEqual(d["partial"][0]["gap"], 250.0)
        self.assertAlmostEqual(d["top_up_cost"], 250.0)

    def test_an_oversized_position_is_full_and_never_negative_gap(self):
        """A winner that ran to 9% needs no cash; it must not show up as a
        credit that cancels out another name's shortfall."""
        d = compute_deployment({"A": _pos(900.0), "B": _pos(100.0)},
                               self.net_liq, 0.0, 5.0)
        self.assertEqual(d["full_count"], 1)
        self.assertAlmostEqual(d["top_up_cost"], 400.0)

    def test_partials_are_ordered_by_the_largest_gap(self):
        d = compute_deployment(
            {"A": _pos(100.0), "B": _pos(400.0), "C": _pos(250.0)},
            self.net_liq, 0.0, 5.0)
        self.assertEqual([p["ticker"] for p in d["partial"]], ["A", "C", "B"])


class TestWhatTheCashCovers(unittest.TestCase):
    def test_cash_left_after_topping_up_funds_whole_new_positions(self):
        # target 500; A needs 400; cash 1,500 → 1,100 left → 2.2 new positions
        d = compute_deployment({"A": _pos(100.0)}, 10_000.0, 1_500.0, 5.0)
        self.assertAlmostEqual(d["top_up_cost"], 400.0)
        self.assertAlmostEqual(d["new_positions_affordable"], 2.2)

    def test_cash_short_of_the_top_ups_funds_no_new_positions(self):
        """And says so rather than reporting a negative count."""
        d = compute_deployment({"A": _pos(100.0)}, 10_000.0, 200.0, 5.0)
        self.assertAlmostEqual(d["top_up_cost"], 400.0)
        self.assertEqual(d["new_positions_affordable"], 0.0)
        self.assertFalse(d["cash_covers_top_ups"])

    def test_fully_deployed_pct_is_where_spending_every_dollar_lands_you(self):
        """The number the card exists for: how little room is left once the
        cash is committed to the names already owned."""
        d = compute_deployment({"A": _pos(8_000.0)}, 10_000.0, 2_000.0, 5.0)
        self.assertAlmostEqual(d["fully_deployed_pct"], 100.0)


class TestBelowBuyPrice(unittest.TestCase):
    def test_positions_under_their_watchlist_buy_price_are_counted(self):
        """Room to add and reason to add are different questions; this answers
        the second."""
        held = {"A": _pos(100.0, "A"), "B": _pos(100.0, "B")}
        prices = {"A": 90.0, "B": 120.0}
        buy = {"A": 100.0, "B": 100.0}
        d = compute_deployment(held, 10_000.0, 0.0, 5.0,
                               prices=prices, buy_prices=buy)
        self.assertEqual(d["below_buy"], ["A"])

    def test_a_name_with_no_buy_price_is_not_counted_either_way(self):
        """No valuation is not the same as "not a buy" — leaving it out of the
        count is honest; putting it in either bucket is a claim we can't
        support."""
        held = {"A": _pos(100.0, "A")}
        d = compute_deployment(held, 10_000.0, 0.0, 5.0,
                               prices={"A": 90.0}, buy_prices={})
        self.assertEqual(d["below_buy"], [])
        self.assertEqual(d["valued_count"], 0)


class TestDisplayBasis(unittest.TestCase):
    """Turning a signed cash flow into a price you can read off a column."""

    def test_cash_paid_out_becomes_a_positive_price(self):
        """8 NFLX at 67.73 costs -541.82 in cash; the cost basis is 67.73, not
        -67.73. The portfolio table printed the raw cash figure and so showed a
        negative price in the Wheel Basis column."""
        self.assertAlmostEqual(display_basis(-67.727), 67.727)

    def test_premiums_beyond_the_share_cost_leave_a_negative_basis(self):
        """Collect more in premium than the shares cost and the basis really is
        below zero — you are net paid to hold them. abs() would have reported
        +5.00 here, which reads as a cost and inverts the meaning."""
        self.assertAlmostEqual(display_basis(5.0), -5.0)

    def test_zero_stays_zero(self):
        self.assertEqual(display_basis(0.0), 0.0)


def _eq(qty, price, action="Buy to Open", txn="Trade", d=None):
    return {"instrument_type": "Equity", "quantity": qty, "price": price,
            "action": action, "type": txn, "date": d or date(2026, 1, 1),
            "net_value": -qty * price if "Buy" in action else qty * price}


class TestHeldShareCost(unittest.TestCase):
    """Cost of the shares still held, FIFO.

    Each expected value below is Tastytrade's own average-open-price for the
    position on 2026-08-11, so these are checked against the broker rather than
    against our own arithmetic.
    """

    def test_a_single_lot(self):
        # NFLX: bought 8 @ 67.727 on 2026-07-20, never touched since.
        cost, shares = held_share_cost([_eq(8, 67.727)])
        self.assertEqual(shares, 8)
        self.assertAlmostEqual(cost / shares, 67.727, places=3)

    def test_two_lots_average(self):
        # MSFT: 1 @ 359.39 and 1 @ 400.2435 -> TT reports 379.81675.
        cost, shares = held_share_cost([_eq(1, 359.39), _eq(1, 400.2435)])
        self.assertAlmostEqual(cost / shares, 379.81675, places=4)

    def test_a_sale_retires_the_oldest_lot_first(self):
        """IBIT: assigned 100 @ 56.00, bought 20 @ 35.8889, sold 20 @ 36.595.

        FIFO takes the 20 out of the 56.00 lot, leaving 80 @ 56 + 20 @ 35.89 =
        51.978 — exactly what Tastytrade reports. Averaging every buy in the
        cycle instead gave 52.65, a purchase price for 120 shares of which 20
        are gone.
        """
        trades = [
            _eq(100, 56.00, action="Buy to Open", txn="Receive Deliver"),
            _eq(20, 35.8889),
            _eq(20, 36.595, action="Sell to Close"),
        ]
        cost, shares = held_share_cost(trades)
        self.assertEqual(shares, 100)
        self.assertAlmostEqual(cost / shares, 51.97778, places=4)

    def test_dividends_are_not_lots(self):
        """A dividend arrives as an Equity row with no quantity. Counting it
        would shift the purchase price of shares it never bought."""
        div = {"instrument_type": "Equity", "quantity": 0.0, "price": 0.0,
               "action": "", "type": "Money Movement", "net_value": 0.91,
               "label": "Dividend"}
        cost, shares = held_share_cost([_eq(1, 100.0), div])
        self.assertEqual(shares, 1)
        self.assertAlmostEqual(cost, 100.0)

    def test_selling_everything_leaves_nothing(self):
        cost, shares = held_share_cost(
            [_eq(10, 50.0), _eq(10, 55.0, action="Sell to Close")])
        self.assertEqual(shares, 0)
        self.assertEqual(cost, 0.0)

    def test_selling_more_than_held_does_not_go_negative(self):
        """History can start mid-position; a sell with no matching lot must not
        invent a negative holding."""
        cost, shares = held_share_cost([_eq(5, 50.0, action="Sell to Close")])
        self.assertEqual(shares, 0)
        self.assertEqual(cost, 0.0)


class TestHasOptionLegs(unittest.TestCase):
    def test_a_plain_buy_is_not_a_wheel(self):
        """NFLX and MSFT were bought outright and never had an option written
        against them. Presenting an adjusted 'wheel' basis for them describes a
        trade that never happened."""
        self.assertFalse(has_option_legs([_eq(8, 67.727)]))

    def test_any_option_leg_makes_it_one(self):
        trades = [_eq(100, 56.0), {"instrument_type": "Equity Option",
                                   "quantity": 1, "price": 0.61,
                                   "action": "Sell to Open", "type": "Trade",
                                   "net_value": 59.88}]
        self.assertTrue(has_option_legs(trades))


class TestFifoRealized(unittest.TestCase):
    """Realized equity P/L per sale, on the same lots the broker used."""

    def test_a_sale_is_priced_against_the_oldest_lot(self):
        """IBIT sold 20 at 36.595 while holding 100 @ 56.00 and 20 @ 35.8889.
        FIFO takes them out of the 56.00 lot: 731.90 - 1120.00 = -388.10, which
        is what Tastytrade booked. Running average cost gave -321.94 for the
        same sale, so the app disagreed with the statement it was meant to
        reconcile against."""
        trades = [
            _eq(100, 56.00, action="Buy to Open", txn="Receive Deliver"),
            _eq(20, 35.8889),
            _eq(20, 36.595, action="Sell to Close"),
        ]
        sales = fifo_realized(trades)
        self.assertEqual(len(sales), 1)
        self.assertAlmostEqual(sales[0]["realized"], -388.10, places=1)

    def test_a_dividend_credit_is_not_a_sale(self):
        """It arrives as a positive Equity row with no quantity. Treated as a
        sale of nothing it booked its full value as equity profit — while the
        same money was already counted in the dividend total."""
        div = {"instrument_type": "Equity", "quantity": 0.0, "price": 0.0,
               "action": "", "type": "Money Movement", "net_value": 0.91}
        self.assertEqual(fifo_realized([_eq(1, 100.0), div]), [])

    def test_a_dividend_debit_is_not_a_purchase(self):
        """Adding it to cost with no shares raises the basis of every share
        already held, so it leaked into later sales too."""
        div = {"instrument_type": "Equity", "quantity": 0.0, "price": 0.0,
               "action": "", "type": "Money Movement", "net_value": -0.27}
        trades = [_eq(10, 100.0), div, _eq(10, 110.0, action="Sell to Close")]
        self.assertAlmostEqual(fifo_realized(trades)[0]["realized"], 100.0)

    def test_each_sale_is_reported_with_its_own_date(self):
        """The monthly and weekly views bucket by date, so a sale has to carry
        one rather than being folded into a running total."""
        trades = [_eq(10, 50.0, d=date(2026, 1, 5)),
                  _eq(4, 60.0, action="Sell to Close", d=date(2026, 3, 2)),
                  _eq(6, 70.0, action="Sell to Close", d=date(2026, 4, 9))]
        sales = fifo_realized(trades)
        self.assertEqual([round(s["realized"], 2) for s in sales], [40.0, 120.0])
        self.assertEqual([s["date"] for s in sales],
                         [date(2026, 3, 2), date(2026, 4, 9)])

    def test_a_sale_spanning_two_lots_prices_each_at_its_own_cost(self):
        trades = [_eq(10, 50.0), _eq(10, 60.0),
                  _eq(15, 70.0, action="Sell to Close")]
        # 10 from the 50 lot (+200) and 5 from the 60 lot (+50)
        self.assertAlmostEqual(fifo_realized(trades)[0]["realized"], 250.0)

    def test_a_sale_with_no_lot_to_match_realizes_nothing(self):
        """History can start mid-position. Booking the whole proceeds as profit
        would invent a gain out of a missing purchase."""
        self.assertEqual(fifo_realized([_eq(5, 50.0, action="Sell to Close")]), [])


class TestOpenLots(unittest.TestCase):
    def test_lots_keep_their_purchase_date(self):
        """Relative performance is measured from the day the money went in, so
        a lot without its date cannot be compared to anything."""
        trades = [_eq(10, 50.0, d=date(2025, 1, 6)),
                  _eq(5, 80.0, d=date(2026, 3, 2))]
        lots = open_lots(trades)
        self.assertEqual([(lot["quantity"], lot["price"], lot["date"]) for lot in lots],
                         [(10, 50.0, date(2025, 1, 6)), (5, 80.0, date(2026, 3, 2))])

    def test_a_sale_removes_the_oldest_lot_and_its_date_with_it(self):
        """After selling the 2025 lot the position is 2026 money, and its
        performance should be measured from 2026."""
        trades = [_eq(10, 50.0, d=date(2025, 1, 6)),
                  _eq(5, 80.0, d=date(2026, 3, 2)),
                  _eq(10, 60.0, action="Sell to Close", d=date(2026, 4, 1))]
        lots = open_lots(trades)
        self.assertEqual([lot["date"] for lot in lots], [date(2026, 3, 2)])


class TestRelativePerformance(unittest.TestCase):
    # SPY closes used by every case below
    INDEX = {date(2025, 1, 6): 100.0, date(2026, 3, 2): 120.0,
             date(2026, 8, 11): 150.0}

    def test_a_single_lot_is_measured_against_the_index_over_its_own_window(self):
        """Bought when SPY was 100, SPY now 150: the index did +50%. The stock
        went 50 -> 90, or +80%. Beating it by 30 points."""
        lots = [{"quantity": 10, "price": 50.0, "date": date(2025, 1, 6)}]
        r = relative_performance(lots, 90.0, self.INDEX, date(2026, 8, 11))
        self.assertAlmostEqual(r["position_return"], 80.0)
        self.assertAlmostEqual(r["index_return"], 50.0)
        self.assertAlmostEqual(r["alpha"], 30.0)

    def test_two_lots_are_weighted_by_money_not_by_count(self):
        """A $500 lot from 2025 and a $400 lot from 2026 each faced a different
        index window. Averaging the two returns equally would let a small late
        top-up drag the verdict on a large long-held position."""
        lots = [{"quantity": 10, "price": 50.0, "date": date(2025, 1, 6)},
                {"quantity": 5, "price": 80.0, "date": date(2026, 3, 2)}]
        r = relative_performance(lots, 90.0, self.INDEX, date(2026, 8, 11))
        # index: +50% on 500 of cost, +25% on 400 -> 38.9% weighted
        self.assertAlmostEqual(r["index_return"], 38.89, places=1)
        # position: 90 vs a 60.0 weighted cost -> +50%
        self.assertAlmostEqual(r["position_return"], 50.0, places=1)
        self.assertAlmostEqual(r["alpha"], 11.11, places=1)

    def test_the_index_close_falls_back_to_the_nearest_earlier_day(self):
        """Buy on a Saturday and there is no close for that date. Skipping the
        lot would silently drop it from the comparison."""
        lots = [{"quantity": 1, "price": 50.0, "date": date(2025, 1, 8)}]
        r = relative_performance(lots, 75.0, self.INDEX, date(2026, 8, 11))
        self.assertAlmostEqual(r["index_return"], 50.0)

    def test_a_lot_older_than_the_index_history_is_reported_not_guessed(self):
        """Anchoring it to the oldest close we happen to have would understate
        the index and hand the position free alpha."""
        lots = [{"quantity": 1, "price": 50.0, "date": date(2020, 1, 1)}]
        r = relative_performance(lots, 75.0, self.INDEX, date(2026, 8, 11))
        self.assertIsNone(r["alpha"])
        self.assertEqual(r["uncovered_cost"], 50.0)

    def test_days_held_is_money_weighted(self):
        """So a card can say when a comparison is too young to mean anything."""
        lots = [{"quantity": 10, "price": 50.0, "date": date(2025, 1, 6)},
                {"quantity": 5, "price": 80.0, "date": date(2026, 3, 2)}]
        r = relative_performance(lots, 90.0, self.INDEX, date(2026, 8, 11))
        self.assertEqual(r["days_held"], round((500 * 582 + 400 * 162) / 900))

    def test_no_lots_yields_nothing_rather_than_zero(self):
        """Zero alpha reads as "kept pace"; there is nothing to keep pace."""
        r = relative_performance([], 90.0, self.INDEX, date(2026, 8, 11))
        self.assertIsNone(r["alpha"])


if __name__ == "__main__":
    unittest.main()
