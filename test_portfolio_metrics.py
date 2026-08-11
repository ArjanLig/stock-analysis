"""Tests for the deployment / dry-powder figures on the portfolio page."""

import unittest

from portfolio_metrics import compute_deployment


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


if __name__ == "__main__":
    unittest.main()
