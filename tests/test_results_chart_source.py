"""The Results chart has to plot the same accounts its headline counts.

It did not. Under an Overview headline reading $29,873 the line fell to
$7,000, because the chart called the single-broker history while the figures
above it called the combined one. After money moved from Tastytrade to
Trading 212 the drop at one account was drawn and the rise at the other was
not — a chart that was wrong in exactly the way that looks like a crash.

Source-level assertions: the selection lives inline in the Streamlit script
and cannot be imported without a running session.
"""

import re

import broker_adapter


def _chart_block():
    """The Net Liq History chart block from streamlit_app.py."""
    src = open("streamlit_app.py").read().split("\n")
    start = next(i for i, line in enumerate(src)
                 if "Net Liq History chart" in line)
    einde = next(i for i in range(start + 1, len(src))
                 if "fig_liq.update_layout" in src[i])
    return "\n".join(src[start:einde])


def test_the_chart_follows_the_selected_tab():
    blok = _chart_block()
    assert "fetch_all_net_liq_history(api_time_back)" in blok
    assert '_res_view == "Overview"' in blok


def test_the_cache_key_carries_the_view():
    """Without the view in the key, switching tabs kept showing whichever
    broker happened to load first — the same wrong-account bug, arriving by
    a different route."""
    blok = _chart_block()
    m = re.search(r'cache_key = f"([^"]+)"', blok)
    assert m, "cache_key not found"
    assert "_res_view" in m.group(1)


class TestMergeCarriesAccountsForward:
    """The merge is what makes an Overview curve correct; these pin the two
    properties the chart now depends on."""

    def test_a_gap_carries_the_last_value_rather_than_zero(self):
        """Brokers print on their own grids. Treating a missing day as zero
        drops a whole account out of the curve for a weekend."""
        a = [{"time": "2026-01-01", "close": 100.0},
             {"time": "2026-01-03", "close": 110.0}]
        b = [{"time": "2026-01-01", "close": 50.0},
             {"time": "2026-01-02", "close": 50.0},
             {"time": "2026-01-03", "close": 50.0}]
        out = {p["time"]: p["close"] for p in broker_adapter.merge_net_liq_series([a, b])}
        assert out["2026-01-02"] == 150.0

    def test_an_account_counts_only_from_its_own_first_point(self):
        """Back-filling a new account invents money that was not there."""
        oud = [{"time": "2026-01-01", "close": 100.0},
               {"time": "2026-02-01", "close": 100.0}]
        nieuw = [{"time": "2026-02-01", "close": 40.0}]
        out = {p["time"]: p["close"] for p in broker_adapter.merge_net_liq_series([oud, nieuw])}
        assert out["2026-01-01"] == 100.0
        assert out["2026-02-01"] == 140.0

    def test_money_moving_between_brokers_keeps_the_total_flat(self):
        """The exact scenario the chart got wrong: one account falls, the
        other rises by the same amount, and the total should not move."""
        tastytrade = [{"time": "2026-08-01", "close": 29000.0},
                      {"time": "2026-08-21", "close": 7000.0}]
        t212 = [{"time": "2026-08-01", "close": 0.0},
                {"time": "2026-08-21", "close": 22000.0}]
        out = {p["time"]: p["close"]
               for p in broker_adapter.merge_net_liq_series([tastytrade, t212])}
        assert out["2026-08-01"] == 29000.0
        assert out["2026-08-21"] == 29000.0
