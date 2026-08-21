"""The share count that has to match the price.

An FCF yield is trailing cash flow over today's market capitalisation, so the
share count belongs on the same basis as the quote. The fiscal-year figure is
not: Booking split roughly 25-for-1 in April 2026 and its FY2025 count of 33M,
divided into a full year's cash flow and set against a post-split $210, showed
a 133% yield on the watchlist.

Reading the cover page fixes that and introduces three ways to be wrong, each
of which broke a real ticker before these tests existed.
"""

import gather_data as g


def _facts(rows):
    """dei:EntityCommonStockSharesOutstanding shaped like EDGAR returns it."""
    return {"facts": {"dei": {"EntityCommonStockSharesOutstanding": {
        "units": {"shares": [{"end": end, "val": val} for end, val in rows]}
    }}}}


def test_a_split_after_the_last_annual_report_is_picked_up():
    """Booking's shape: the annual series ends pre-split and the only
    post-split number lives on a quarterly cover page."""
    facts = _facts([("2026-02-10", 31_673_346), ("2026-04-20", 774_878_436)])
    assert g.latest_cover_page_shares(facts, 2025) == 774_878_436


def test_a_cover_page_that_stopped_being_updated_is_refused():
    """Mastercard's newest value is dated 2010 and Comcast's 2009 — they
    simply stopped tagging it. Taking the newest entry regardless of age put
    Mastercard on 122M shares instead of 906M and its FCF yield at 24%.
    None here means the caller keeps the annual series."""
    facts = _facts([("2010-10-27", 122_530_193)])
    assert g.latest_cover_page_shares(facts, 2025) is None


def test_the_boundary_year_counts_as_current():
    """A cover page dated at the fiscal year end is not stale."""
    facts = _facts([("2025-12-31", 500_000_000)])
    assert g.latest_cover_page_shares(facts, 2025) == 500_000_000


def test_share_classes_on_one_date_are_summed():
    """Multi-class filers report a row per class. Choosing between them
    reports a fraction of the company: Comcast's Class A alone is 2.06bn of
    its 3.7bn shares."""
    facts = _facts([("2026-04-20", 2_063_073_161), ("2026-04-20", 1_646_000_000)])
    assert g.latest_cover_page_shares(facts, 2025) == 3_709_073_161


def test_only_the_newest_date_is_summed():
    """Adding older dates in would count the company several times over."""
    facts = _facts([("2024-04-20", 900_000_000), ("2026-04-20", 800_000_000)])
    assert g.latest_cover_page_shares(facts, 2025) == 800_000_000


def test_an_adr_is_converted_to_the_traded_unit(monkeypatch):
    """A foreign filer counts ordinary shares while the price is an ADR.
    Taiwan Semiconductor reports 25.9bn ordinary against a 1:5 ratio, and
    without the conversion its yield came out five times too low."""
    monkeypatch.setattr(g, "apply_adr_share_ratio",
                        lambda shares, ticker: [s / 5 for s in shares])
    facts = _facts([("2025-12-31", 25_932_524_521)])
    assert g.latest_cover_page_shares(facts, 2025, "TSM") == 25_932_524_521 / 5


def test_nothing_to_read_gives_nothing_back():
    """Every unusable case returns None rather than a guess, because the
    caller's fallback — the fiscal-year count — is what it used before and is
    right whenever this is absent."""
    assert g.latest_cover_page_shares(None, 2025) is None
    assert g.latest_cover_page_shares({}, 2025) is None
    assert g.latest_cover_page_shares(_facts([]), 2025) is None
    assert g.latest_cover_page_shares(_facts([("2026-04-20", 1)]), None) is None


def test_a_malformed_entry_does_not_raise():
    """A filer with a missing value must not take the whole fetch down."""
    facts = {"facts": {"dei": {"EntityCommonStockSharesOutstanding": {
        "units": {"shares": [{"end": "2026-04-20"}, {"val": 5}]}}}}}
    assert g.latest_cover_page_shares(facts, 2025) is None
