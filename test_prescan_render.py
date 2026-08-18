"""Parsing a pre-scan section into the parts a card needs."""

import unittest

from prescan_render import parse_verdict_section

_MOAT = """**Moat: Wide 🛡️ · Stable ➡️ · 4/5**

MercadoLibre's **wide moat** comes from an integrated ecosystem where
marketplace, payments, logistics and lending reinforce each other.

- **Ecosystem Flywheel**: more buyers attract sellers who adopt Pago
- **Regulatory Barriers**: licences across LatAm take years for entrants
- **Full-Stack Integration**: no competitor combines all four

**Weakest link:** no switching costs — buyers can leave tomorrow.

## Sources
[1] MELI 10-K - sec.gov
"""

_RISK = """**Risk: Medium 🟡 · Competition**

Performance running is crowded and HOKA's first-mover lead is narrowing.

- **Brand Concentration**: UGG and HOKA are 97% of revenue
- **Tariff Exposure**: 80bps of gross margin in FY2026
- **Fashion Cycles**: maximalist running could fall out of favour

**What would change this:** gross margin under 54% for two quarters.
"""


class TestMoat(unittest.TestCase):
    def setUp(self):
        self.v = parse_verdict_section(_MOAT)

    def test_the_headline_verdict_is_pulled_out(self):
        self.assertEqual(self.v["label"], "Wide")
        self.assertEqual(self.v["score"], 4.0)
        self.assertEqual(self.v["out_of"], 5.0)

    def test_the_qualifiers_ride_along(self):
        """Direction is part of the verdict — a wide moat narrowing is not the
        same call as a wide moat holding."""
        self.assertIn("Stable", self.v["qualifiers"])

    def test_the_summary_is_the_prose_before_the_bullets(self):
        self.assertTrue(self.v["summary"].startswith("MercadoLibre"))
        self.assertNotIn("- **", self.v["summary"])

    def test_the_bullets_come_back_split_from_their_labels(self):
        self.assertEqual(len(self.v["bullets"]), 3)
        self.assertEqual(self.v["bullets"][0]["label"], "Ecosystem Flywheel")
        self.assertIn("Pago", self.v["bullets"][0]["text"])

    def test_the_mandatory_closing_line_is_kept_separate(self):
        """Weakest link and "what would change this" are the two lines most
        worth not losing in a wall of text."""
        self.assertEqual(self.v["footer_label"], "Weakest link")
        self.assertIn("switching costs", self.v["footer_text"])

    def test_sources_stay_out_of_the_card(self):
        self.assertNotIn("sec.gov", self.v["summary"])
        self.assertNotIn("sec.gov", self.v["footer_text"])


class TestRisk(unittest.TestCase):
    def test_a_verdict_without_a_score_still_parses(self):
        """Risk is a three-level rating. There is no 0-5 score to show, and
        inventing one would put false precision on the card."""
        v = parse_verdict_section(_RISK)
        self.assertEqual(v["label"], "Medium")
        self.assertIsNone(v["score"])
        self.assertIn("Competition", v["qualifiers"])
        self.assertEqual(len(v["bullets"]), 3)
        self.assertEqual(v["footer_label"], "What would change this")


class TestOldFormat(unittest.TestCase):
    def test_a_section_in_the_previous_format_is_left_alone(self):
        """Sixty-odd tickers hold output from the old templates. Half-parsing
        it into a card would look like a rendering bug; returning None lets the
        caller fall back to plain markdown."""
        old = ("# 🏰 Moat Analysis: Deckers (DECK)\n"
               "  * **Moat Size:** Narrow 🤏\n"
               "  * **Summary:** Deckers possesses a narrow moat...\n"
               "## ⚓️ Switching Costs\n  * **Assessment:** ❌ Not Present\n")
        self.assertIsNone(parse_verdict_section(old))

    def test_empty_input(self):
        self.assertIsNone(parse_verdict_section(""))
        self.assertIsNone(parse_verdict_section(None))

    def test_a_verdict_line_with_no_bullets_is_not_a_card(self):
        """A card with an empty body is worse than the text it replaced."""
        self.assertIsNone(parse_verdict_section("**Moat: Wide 🛡️ · 4/5**\n\nJust prose.\n"))


class TestGauge(unittest.TestCase):
    def test_the_arc_fills_in_proportion_to_the_score(self):
        from prescan_render import gauge_fraction
        self.assertAlmostEqual(gauge_fraction(4.0, 5.0), 0.8)
        self.assertAlmostEqual(gauge_fraction(0.0, 5.0), 0.0)

    def test_a_score_beyond_the_scale_is_clamped(self):
        from prescan_render import gauge_fraction
        self.assertAlmostEqual(gauge_fraction(7.0, 5.0), 1.0)
        self.assertAlmostEqual(gauge_fraction(-1.0, 5.0), 0.0)


if __name__ == "__main__":
    unittest.main()
