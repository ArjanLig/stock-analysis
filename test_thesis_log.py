"""Tests for the DCF assumption log."""

import unittest

from config_store import ASSUMPTION_LOG_KEY, ASSUMPTION_LOG_MAX, append_assumption_snapshot
from thesis import thesis_vs_history


def _cfg(growth=None, margins=None, **kw):
    out = {
        "base_year": 2026,
        "base_revenue": 5472,
        "base_op_margin": 0.231,
        "revenue_growth": growth or [0.08, 0.08, 0.06],
        "op_margins": margins or [0.215, 0.22, 0.22],
        "terminal_growth": 0.0363,
        "terminal_margin": 0.18,
    }
    out.update(kw)
    return out


class TestFirstSnapshot(unittest.TestCase):
    def test_the_current_assumptions_become_entry_one(self):
        """Nothing kept a record before, so the vintage on file when this
        arrives is the earliest that can ever be recovered."""
        log = append_assumption_snapshot({}, _cfg(), today="2026-08-13")
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["as_of"], "2026-08-13")
        self.assertEqual(log[0]["base_year"], 2026)
        self.assertEqual(log[0]["revenue_growth"], [0.08, 0.08, 0.06])

    def test_it_records_the_fair_value_those_assumptions_produced(self):
        """So the log answers a second question for free: whether your own
        fair value creeps up to meet the price each time you revisit."""
        cfg = _cfg(valuation_summary={"weighted_fv_mid": 124.14})
        log = append_assumption_snapshot({}, cfg, today="2026-08-13")
        self.assertAlmostEqual(log[0]["fv_mid"], 124.14)

    def test_a_config_with_no_assumptions_logs_nothing(self):
        """A ticker saved before its DCF was built has nothing to record, and
        an entry of nulls would look like a thesis that was actually set."""
        self.assertEqual(append_assumption_snapshot({}, {"ticker": "X"}), [])


class TestOnlyOnChange(unittest.TestCase):
    def test_resaving_the_same_assumptions_adds_nothing(self):
        """save_config runs on every valuation refresh. Appending each time
        would bury the handful of real revisions under hundreds of rows."""
        first = append_assumption_snapshot({}, _cfg(), today="2026-08-13")
        again = append_assumption_snapshot(
            {ASSUMPTION_LOG_KEY: first}, _cfg(), today="2026-09-01")
        self.assertEqual(len(again), 1)

    def test_a_changed_growth_path_is_a_new_entry(self):
        first = append_assumption_snapshot({}, _cfg(), today="2026-08-13")
        second = append_assumption_snapshot(
            {ASSUMPTION_LOG_KEY: first},
            _cfg(growth=[0.10, 0.09, 0.07]), today="2027-02-01")
        self.assertEqual(len(second), 2)
        self.assertEqual(second[-1]["revenue_growth"], [0.10, 0.09, 0.07])

    def test_a_new_base_year_is_a_new_entry_even_at_the_same_growth(self):
        """Rolling the model forward a year is the event the whole log exists
        to capture: it is what silently erased the old assumptions."""
        first = append_assumption_snapshot({}, _cfg(), today="2026-08-13")
        second = append_assumption_snapshot(
            {ASSUMPTION_LOG_KEY: first}, _cfg(base_year=2027), today="2027-08-01")
        self.assertEqual(len(second), 2)

    def test_a_moved_fair_value_alone_is_not_a_revision(self):
        """Fair value drifts with the risk-free rate and the share price
        without anyone changing their mind. Logging that would make every
        refresh look like a rethink."""
        first = append_assumption_snapshot(
            {}, _cfg(valuation_summary={"weighted_fv_mid": 124.14}),
            today="2026-08-13")
        second = append_assumption_snapshot(
            {ASSUMPTION_LOG_KEY: first},
            _cfg(valuation_summary={"weighted_fv_mid": 131.02}),
            today="2026-09-20")
        self.assertEqual(len(second), 1)


class TestBounds(unittest.TestCase):
    def test_the_log_stops_growing_at_the_cap(self):
        log = []
        for i in range(ASSUMPTION_LOG_MAX + 10):
            log = append_assumption_snapshot(
                {ASSUMPTION_LOG_KEY: log},
                _cfg(growth=[0.01 * i, 0.02, 0.03]),
                today=f"2026-08-{(i % 28) + 1:02d}")
        self.assertEqual(len(log), ASSUMPTION_LOG_MAX)

    def test_the_oldest_entries_are_the_ones_dropped(self):
        """The recent thesis is the one being graded; the first vintage is the
        one worth losing last, but a bound has to give somewhere."""
        log = []
        for i in range(ASSUMPTION_LOG_MAX + 5):
            log = append_assumption_snapshot(
                {ASSUMPTION_LOG_KEY: log},
                _cfg(growth=[0.01 * i, 0.02, 0.03]),
                today="2026-08-13")
        self.assertAlmostEqual(log[-1]["revenue_growth"][0],
                               0.01 * (ASSUMPTION_LOG_MAX + 4))

    def test_a_corrupt_log_is_replaced_rather_than_crashing_the_save(self):
        """A save that raises loses the user's actual edit. The log is the
        least important thing in the config."""
        log = append_assumption_snapshot(
            {ASSUMPTION_LOG_KEY: "not a list"}, _cfg(), today="2026-08-13")
        self.assertEqual(len(log), 1)


class TestUnderwrittenVsDelivered(unittest.TestCase):
    """How hard the assumption is against what the business has done."""

    def test_a_thesis_below_the_track_record(self):
        """DECK's real config: an 8/8/8/6/6% path against a 14.7% three-year
        CAGR. Underwriting half of what the business delivered is why the fair
        value survives a bad quarter — and it is nowhere on screen today."""
        t = thesis_vs_history([0.08, 0.08, 0.08, 0.06, 0.06], 0.147)
        self.assertAlmostEqual(t["assumed_cagr"], 0.072, places=3)
        self.assertAlmostEqual(t["delivered_cagr"], 0.147)
        self.assertAlmostEqual(t["ratio"], 0.49, places=2)
        self.assertFalse(t["heroic"])

    def test_a_thesis_above_the_track_record_is_flagged(self):
        """15% on a business that has done 5% puts the whole valuation on a
        break in trend."""
        t = thesis_vs_history([0.15] * 5, 0.05)
        self.assertAlmostEqual(t["ratio"], 3.0)
        self.assertTrue(t["heroic"])

    def test_matching_the_record_is_not_heroic(self):
        self.assertFalse(thesis_vs_history([0.10] * 5, 0.10)["heroic"])

    def test_a_shrinking_business_yields_no_ratio(self):
        """Dividing by a negative CAGR gives a number whose sign means nothing.
        Assuming growth where there has been decline is worth seeing, but not
        as a multiple."""
        t = thesis_vs_history([0.08] * 5, -0.04)
        self.assertIsNone(t["ratio"])
        self.assertTrue(t["heroic"])

    def test_no_history_yields_nothing(self):
        self.assertIsNone(thesis_vs_history([0.08] * 5, None))
        self.assertIsNone(thesis_vs_history([], 0.10))


if __name__ == "__main__":
    unittest.main()
