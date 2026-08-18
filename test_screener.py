"""Quality screen: sustained ROCE and a balance sheet without net debt."""

import unittest

from screener import net_debt_latest, screen_quality


def _fund(oi, ta, cl, **kw):
    f = {"years": list(range(2016, 2016 + len(oi))),
         "operating_income": oi, "total_assets": ta, "current_liabilities": cl}
    f.update(kw)
    return f


class TestAverageRoce(unittest.TestCase):
    def test_a_steady_compounder_passes(self):
        # CE = TA - CL - excess cash = 1500 - 500 - 100 = 900, so EBIT 300 is
        # 33.3%. Capital employed strips idle cash here exactly as it does on
        # the watchlist — a company is not less efficient for holding a war
        # chest it does not use.
        r = screen_quality(_fund([300] * 8, [1500] * 8, [500] * 8,
                                 total_debt=[0] * 8, cash=[100] * 8))
        self.assertTrue(r["passes"])
        self.assertAlmostEqual(r["avg_roce"], 33.33, places=1)
        self.assertEqual(r["years_used"], 8)

    def test_the_average_is_what_counts_not_every_year(self):
        """One bad year does not disqualify — the user asked for the average,
        which lets a cyclical through on its record rather than its worst
        moment."""
        r = screen_quality(_fund([300, 300, 300, 20, 300, 300], [1500] * 6,
                                 [500] * 6, total_debt=[0] * 6, cash=[10] * 6))
        self.assertTrue(r["passes"])          # (30*5 + 2)/6 = 25.3%
        self.assertGreater(r["avg_roce"], 20)

    def test_a_business_averaging_under_the_gate_fails(self):
        r = screen_quality(_fund([150] * 6, [1500] * 6, [500] * 6,
                                 total_debt=[0] * 6, cash=[10] * 6))
        self.assertFalse(r["passes"])
        self.assertEqual(r["reason"], "roce_below_gate")

    def test_too_few_years_is_excluded_not_passed(self):
        """Three years of 40% ROCE is not a ten-year record. Letting it through
        on a short window is exactly the false positive a durability screen
        exists to avoid."""
        r = screen_quality(_fund([400] * 3, [1500] * 3, [500] * 3,
                                 total_debt=[0] * 3, cash=[10] * 3))
        self.assertFalse(r["passes"])
        self.assertEqual(r["reason"], "insufficient_history")

    def test_only_the_most_recent_years_count(self):
        """A decade of brilliance in 2010 says nothing about today, so the
        window is capped at the latest max_years."""
        oi = [400] * 6 + [100] * 10        # great long ago, mediocre lately
        r = screen_quality(_fund(oi, [1500] * 16, [500] * 16,
                                 total_debt=[0] * 16, cash=[10] * 16),
                           max_years=10)
        self.assertEqual(r["years_used"], 10)
        self.assertFalse(r["passes"])

    def test_years_with_missing_figures_are_skipped(self):
        r = screen_quality(_fund([300, None, 300, 300, 300, 300], [1500] * 6,
                                 [500] * 6, total_debt=[0] * 6, cash=[10] * 6))
        self.assertEqual(r["years_used"], 5)
        self.assertTrue(r["passes"])


class TestNetDebt(unittest.TestCase):
    def test_cash_and_investments_both_count_against_debt(self):
        """Short-term investments are cash a company parks; ignoring them would
        fail businesses that hold treasuries instead of a bank balance."""
        self.assertAlmostEqual(
            net_debt_latest({"total_debt": [1000], "cash": [400],
                             "short_term_investments": [700]}), -100.0)

    def test_net_debt_fails_the_screen(self):
        r = screen_quality(_fund([300] * 6, [1500] * 6, [500] * 6,
                                 total_debt=[900] * 6, cash=[100] * 6))
        self.assertFalse(r["passes"])
        self.assertEqual(r["reason"], "net_debt")

    def test_only_the_latest_year_is_judged(self):
        """A company that has paid its debt down should pass on where it stands
        now, not on where it stood five years ago — as long as the series got
        there by declining rather than by dropping off a cliff, which is
        indistinguishable from a tag that stopped resolving."""
        r = screen_quality(_fund([300] * 6, [1500] * 6, [500] * 6,
                                 total_debt=[900, 780, 600, 400, 220, 60],
                                 cash=[10, 10, 10, 10, 300, 800]))
        self.assertTrue(r["passes"])

    def test_no_debt_disclosed_is_treated_as_none(self):
        """A filer that never tags debt has none to tag — common for the
        debt-free businesses this screen is looking for."""
        r = screen_quality(_fund([300] * 6, [1500] * 6, [500] * 6,
                                 cash=[100] * 6))
        self.assertTrue(r["passes"])

    def test_missing_cash_is_not_assumed_to_be_zero(self):
        """Without a cash figure the net-debt test cannot be made. Assuming
        zero would fail every company that simply did not tag it."""
        r = screen_quality(_fund([300] * 6, [1500] * 6, [500] * 6,
                                 total_debt=[500] * 6))
        self.assertFalse(r["passes"])
        self.assertEqual(r["reason"], "no_balance_sheet")


class TestAgreementWithTheWatchlist(unittest.TestCase):
    def test_it_uses_the_same_roce_as_the_rest_of_the_app(self):
        """A screener saying 25% where the watchlist says 19% is the two-
        answers-to-one-question problem again."""
        import scorecard_utils
        fund = _fund([300] * 6, [1500] * 6, [500] * 6,
                     total_debt=[0] * 6, cash=[10] * 6)
        mine = screen_quality(fund)["avg_roce"]
        theirs = [scorecard_utils.roce_for_year(fund, i)[0] for i in range(6)]
        self.assertAlmostEqual(mine, sum(theirs) / len(theirs))


if __name__ == "__main__":
    unittest.main()


class TestSuspectDebtTag(unittest.TestCase):
    """A leveraged company passing a no-net-debt screen is the worst error
    this code can make, so an implausible debt series reads as "cannot tell"
    rather than as "no debt"."""

    def test_debt_that_vanishes_is_treated_as_untagged(self):
        """DPZ, from the real data: total_debt runs 4,934 then 15, then 15.
        Domino's carries about $5bn. The tag stopped resolving, and read
        literally it turned a leveraged company into a debt-free one."""
        r = screen_quality(_fund([820, 879, 954, 900, 900, 900],
                                 [1716] * 6, [542] * 6,
                                 total_debt=[4934, 4934, 4934, 4934, 15, 15],
                                 cash=[126] * 6))
        self.assertFalse(r["passes"])
        self.assertEqual(r["reason"], "debt_tag_suspect")

    def test_genuine_deleveraging_still_passes(self):
        """Paying debt down over years is not the same as a tag disappearing:
        the series falls, it does not fall off a cliff."""
        r = screen_quality(_fund([300] * 6, [1500] * 6, [500] * 6,
                                 total_debt=[900, 700, 500, 300, 150, 40],
                                 cash=[100, 200, 300, 400, 600, 900]))
        self.assertTrue(r["passes"])

    def test_a_company_that_never_had_debt_is_unaffected(self):
        """The guard must not punish the businesses the screen is looking for."""
        r = screen_quality(_fund([300] * 6, [1500] * 6, [500] * 6,
                                 total_debt=[0, 0, 0, 0, 0, 0],
                                 cash=[100] * 6))
        self.assertTrue(r["passes"])

    def test_a_small_absolute_drop_is_not_suspicious(self):
        """A fall from 30 to 2 on a 1,500 balance sheet is rounding, not a
        missing five-billion-dollar liability."""
        r = screen_quality(_fund([300] * 6, [1500] * 6, [500] * 6,
                                 total_debt=[30, 30, 30, 30, 30, 2],
                                 cash=[100] * 6))
        self.assertTrue(r["passes"])


class TestBatch(unittest.TestCase):
    """Screening a universe, index by index."""

    _UNIVERSE = {
        "as_of": "2026-08-06",
        "constituents": [
            {"ticker": "GOOD", "name": "Good Co", "gics_sector": "IT",
             "indices": ["sp500", "nasdaq100"]},
            {"ticker": "DEBT", "name": "Levered Co", "gics_sector": "Staples",
             "indices": ["sp500", "dow30"]},
            {"ticker": "GONE", "name": "Broken Co", "gics_sector": "Energy",
             "indices": ["sp500"]},
        ],
    }

    @staticmethod
    def _fetch(ticker):
        if ticker == "GONE":
            raise RuntimeError("EDGAR 404")
        if ticker == "DEBT":
            return _fund([300] * 6, [1500] * 6, [500] * 6,
                         total_debt=[900] * 6, cash=[100] * 6)
        return _fund([300] * 6, [1500] * 6, [500] * 6,
                     total_debt=[0] * 6, cash=[100] * 6)

    def _run(self):
        from screener import compute_screener
        return compute_screener(self._UNIVERSE, fetch=self._fetch, max_workers=2)

    def test_every_name_comes_back_with_its_verdict(self):
        rows = {r["ticker"]: r for r in self._run()["rows"]}
        self.assertTrue(rows["GOOD"]["passes"])
        self.assertFalse(rows["DEBT"]["passes"])
        self.assertEqual(rows["DEBT"]["reason"], "net_debt")

    def test_a_fetch_failure_is_recorded_not_dropped(self):
        """A name that vanishes silently looks identical to a name that
        failed the screen. It has to say which it is."""
        rows = {r["ticker"]: r for r in self._run()["rows"]}
        self.assertEqual(rows["GONE"]["status"], "failed")
        self.assertFalse(rows["GONE"]["passes"])

    def test_index_membership_travels_with_each_row(self):
        """The page filters by index; a row that lost its membership would
        appear nowhere."""
        rows = {r["ticker"]: r for r in self._run()["rows"]}
        self.assertIn("nasdaq100", rows["GOOD"]["indices"])
        self.assertIn("dow30", rows["DEBT"]["indices"])

    def test_the_summary_counts_passes_per_index(self):
        s = self._run()["summary"]
        self.assertEqual(s["passes"], 1)
        self.assertEqual(s["per_index"]["sp500"]["total"], 3)
        self.assertEqual(s["per_index"]["sp500"]["passes"], 1)
        self.assertEqual(s["per_index"]["nasdaq100"]["passes"], 1)
        self.assertEqual(s["per_index"]["dow30"]["passes"], 0)

    def test_the_universe_date_is_kept(self):
        self.assertEqual(self._run()["universe_as_of"], "2026-08-06")
