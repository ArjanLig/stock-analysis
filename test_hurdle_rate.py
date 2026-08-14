"""A fixed hurdle rate as the discount rate.

Third discount philosophy alongside capm and opportunity_cost. Unlike those
two it moves with nothing: not the risk-free rate, not the equity risk
premium, not the company's beta or capital structure. One number, one hurdle,
every name.
"""

import unittest

from dcf_calculator import (DEFAULT_DISCOUNT_MODE, DEFAULT_HURDLE_RATE,
                            compute_cost_of_equity, compute_wacc)


def _cfg(**kw):
    cfg = {
        "risk_free_rate": 0.0461,
        "erp": 0.0445,
        "credit_spread": 0.004,
        "tax_rate": 0.23,
        "debt_market_value": 2000.0,
        "equity_market_value": 14300.0,
        "sector_betas": [["Shoe", 1.14, 1.0]],
        "stock_price": 99.31,
        "shares_outstanding": 144,
    }
    cfg.update(kw)
    return cfg


class TestHurdleMode(unittest.TestCase):
    def test_the_hurdle_is_the_discount_rate(self):
        self.assertAlmostEqual(compute_wacc(_cfg(discount_mode="hurdle")),
                               DEFAULT_HURDLE_RATE)

    def test_it_is_the_default_for_a_config_that_says_nothing(self):
        """Every watchlist name should land on the same hurdle without each
        config having to carry the setting."""
        self.assertEqual(DEFAULT_DISCOUNT_MODE, "hurdle")
        self.assertAlmostEqual(compute_wacc(_cfg()), DEFAULT_HURDLE_RATE)

    def test_debt_does_not_lower_it(self):
        """The point of a hurdle: cheap debt and its tax shield are not a
        reason to demand less from a business."""
        self.assertAlmostEqual(
            compute_wacc(_cfg(discount_mode="hurdle", debt_market_value=0.0)),
            compute_wacc(_cfg(discount_mode="hurdle",
                              debt_market_value=50_000.0)))

    def test_beta_does_not_move_it(self):
        a = compute_wacc(_cfg(discount_mode="hurdle",
                              sector_betas=[["Utility", 0.4, 1.0]]))
        b = compute_wacc(_cfg(discount_mode="hurdle",
                              sector_betas=[["Crypto", 2.8, 1.0]]))
        self.assertAlmostEqual(a, b)

    def test_a_rate_change_does_not_move_it(self):
        """Which is the whole difference from opportunity_cost: that one is
        rf + ERP and drifts every time the Treasury does."""
        a = compute_wacc(_cfg(discount_mode="hurdle", risk_free_rate=0.01))
        b = compute_wacc(_cfg(discount_mode="hurdle", risk_free_rate=0.08))
        self.assertAlmostEqual(a, b)

    def test_a_config_may_carry_its_own_hurdle(self):
        """So one name can be held to a higher bar without moving everything."""
        cfg = _cfg(discount_mode="hurdle", hurdle_rate=0.12)
        self.assertAlmostEqual(compute_wacc(cfg), 0.12)

    def test_cost_of_equity_reports_the_hurdle_too(self):
        """Nothing should show a cost of equity that the DCF is not using."""
        self.assertAlmostEqual(
            compute_cost_of_equity(_cfg(discount_mode="hurdle")),
            DEFAULT_HURDLE_RATE)


class TestOtherModesStillWork(unittest.TestCase):
    def test_capm_is_unchanged(self):
        w = compute_wacc(_cfg(discount_mode="capm"))
        self.assertGreater(w, 0.05)
        self.assertLess(w, 0.15)
        self.assertNotAlmostEqual(w, DEFAULT_HURDLE_RATE, places=4)

    def test_opportunity_cost_is_unchanged(self):
        cfg = _cfg(discount_mode="opportunity_cost")
        self.assertAlmostEqual(compute_wacc(cfg),
                               cfg["risk_free_rate"] + cfg["erp"])


if __name__ == "__main__":
    unittest.main()
