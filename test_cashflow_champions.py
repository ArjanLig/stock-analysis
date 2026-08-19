"""Tests for the Cashflow Champions screen.

Two halves, mirroring the module:
  • Pure ranking math on a synthetic universe (no network) — exact, deterministic.
  • Robustness of the batch pipeline: partial failures don't abort the run,
    financials are excluded and counted, and the disk cache prevents re-fetch.
"""
import json

import cashflow_champions as cc
from cashflow_champions import ChampRow


# ── Pure ranking math ──────────────────────────────────────────────────────────

def test_percentiles_monotonic_and_ties():
    assert cc._percentiles([]) == []
    assert cc._percentiles([42.0]) == [1.0]
    # strictly increasing → 0 … 1
    assert cc._percentiles([1, 2, 3]) == [0.0, 0.5, 1.0]
    # ties share the average rank
    p = cc._percentiles([5, 5, 9])
    assert p[0] == p[1] == 0.25
    assert p[2] == 1.0


def _row(t, cfo, ta, mc, sic=None, sector="Test Sector"):
    """Default every row into one sector: within-sector ranking over a single
    group is arithmetically identical to the old global ranking, so the maths
    tests below keep asserting exactly what they always did."""
    return ChampRow(ticker=t, cfo=cfo, total_assets=ta, market_cap=mc, sic=sic,
                    sector=sector)


def test_rank_synthetic_universe_orders_and_cuts_top_20pct():
    # 5 clean names. Champion = high Cash ROA (cfo/assets) AND cheap (low P/CF).
    # GOOD: high cash ROA + cheap. JUNK: low cash ROA + expensive.
    rows = [
        _row("GOOD", cfo=100, ta=200, mc=500),   # ROA 0.50, yield 0.20
        _row("OKAY", cfo=80, ta=400, mc=800),    # ROA 0.20, yield 0.10
        _row("MEH",  cfo=60, ta=600, mc=1200),   # ROA 0.10, yield 0.05
        _row("WEAK", cfo=40, ta=800, mc=1600),   # ROA 0.05, yield 0.025
        _row("JUNK", cfo=20, ta=1000, mc=2000),  # ROA 0.02, yield 0.01
    ]
    out = cc.rank_universe(rows, exclude_financials=True, top_pct=0.20)
    by_t = {r.ticker: r for r in out["rows"]}

    assert by_t["GOOD"].rank == 1
    assert by_t["JUNK"].rank == 5
    assert by_t["GOOD"].is_champion is True
    # top 20% of 5 = ceil(1.0) = 1 champion
    assert out["summary"]["champions"] == 1
    assert sum(1 for r in out["rows"] if r.is_champion) == 1
    # ratios computed correctly
    assert abs(by_t["GOOD"].cash_roa - 0.5) < 1e-9
    assert abs(by_t["GOOD"].price_to_cf - 5.0) < 1e-9


def test_negative_and_missing_cfo_are_excluded_not_ranked():
    rows = [
        _row("OK", cfo=100, ta=200, mc=500),
        _row("NEG", cfo=-50, ta=200, mc=500),
        _row("MISS", cfo=None, ta=200, mc=500),
        _row("NOCAP", cfo=100, ta=200, mc=None),
    ]
    out = cc.rank_universe(rows)
    by_t = {r.ticker: r for r in out["rows"]}
    assert by_t["OK"].status == "ok"
    assert by_t["NEG"].reason == "negative_cfo"
    assert by_t["MISS"].reason == "missing_data"
    assert by_t["NOCAP"].reason == "missing_data"
    # only the clean name is ranked; all four still present in the output
    assert out["summary"]["ranked"] == 1
    assert len(out["rows"]) == 4


def test_implausible_pcf_excluded_as_data_quality():
    # A P/CF below the floor (market cap ≈ CFO) is a data error (e.g. an uncaught
    # multi-class share undercount) — excluded, not ranked #1.
    rows = [
        _row("REAL", cfo=100, ta=200, mc=900),   # P/CF 9 — fine
        _row("BADMC", cfo=100, ta=200, mc=120),   # P/CF 1.2 — implausible
    ]
    out = cc.rank_universe(rows)
    by_t = {r.ticker: r for r in out["rows"]}
    assert by_t["BADMC"].reason == "data_quality"
    assert by_t["BADMC"].rank is None
    assert out["summary"]["ranked"] == 1


def test_financials_excluded_and_counted():
    rows = [
        _row("TECH", cfo=100, ta=200, mc=500, sic=7372),   # software
        _row("BANK", cfo=100, ta=200, mc=500, sic=6020),   # national commercial bank
        _row("INSUR", cfo=100, ta=200, mc=500, sic=6311),  # life insurance
    ]
    out = cc.rank_universe(rows, exclude_financials=True)
    by_t = {r.ticker: r for r in out["rows"]}
    assert by_t["BANK"].reason == "financial"
    assert by_t["INSUR"].reason == "financial"
    assert out["summary"]["excluded_financials"] == 2
    assert out["summary"]["ranked"] == 1

    # with the flag off, financials are ranked
    rows2 = [_row("BANK", cfo=100, ta=200, mc=500, sic=6020)]
    out2 = cc.rank_universe(rows2, exclude_financials=False)
    assert out2["summary"]["ranked"] == 1


# ── Sector-relative ranking ────────────────────────────────────────────────────

def test_percentiles_are_computed_within_sector_not_globally():
    """The whole point of the change: a name is measured against its own sector.
    ENERGY_TOP has the best absolute ratios in the universe but is only
    mid-pack among its peers, so it must not outrank the best software name."""
    energy = [_row(f"E{i}", cfo=100 - i, ta=200, mc=400 + i * 10, sector="Energy")
              for i in range(5)]
    software = [_row(f"S{i}", cfo=20 - i, ta=400, mc=2000 + i * 100,
                     sector="Information Technology") for i in range(5)]
    out = cc.rank_universe(energy + software, top_pct=0.20)
    by_t = {r.ticker: r for r in out["rows"]}

    # Best of each sector ranks 1 *within its sector*
    assert by_t["E0"].sector_rank == 1
    assert by_t["S0"].sector_rank == 1
    assert by_t["E0"].sector_size == 5
    assert by_t["S0"].sector_size == 5
    # Each sector gets its own champion, even though every energy name has
    # better absolute ratios than every software name.
    assert by_t["E0"].is_champion is True
    assert by_t["S0"].is_champion is True
    assert out["summary"]["champions"] == 2


def test_champion_flag_is_top_pct_of_each_sector():
    big = [_row(f"B{i}", cfo=100 - i, ta=200, mc=500, sector="Industrials")
           for i in range(10)]
    small = [_row(f"S{i}", cfo=100 - i, ta=200, mc=500, sector="Utilities")
             for i in range(5)]
    out = cc.rank_universe(big + small, top_pct=0.20)
    champs = [r for r in out["rows"] if r.is_champion]
    # ceil(10 * .2) = 2 from Industrials, ceil(5 * .2) = 1 from Utilities
    assert sorted(r.ticker for r in champs) == ["B0", "B1", "S0"]
    assert out["summary"]["champions"] == 3


def test_sector_below_minimum_size_yields_no_champions():
    """Real Estate held a single name (CSGP) in the live universe — a group of
    one would otherwise crown itself. Ranked, but never flagged."""
    big = [_row(f"B{i}", cfo=100 - i, ta=200, mc=500, sector="Industrials")
           for i in range(10)]
    tiny = [_row("LONE", cfo=100, ta=150, mc=400, sector="Real Estate")]
    out = cc.rank_universe(big + tiny, top_pct=0.20)
    by_t = {r.ticker: r for r in out["rows"]}

    assert by_t["LONE"].status == "ok"          # still screened and ranked
    assert by_t["LONE"].sector_rank == 1
    assert by_t["LONE"].sector_size == 1
    assert by_t["LONE"].is_champion is False    # but never a champion
    assert by_t["LONE"].reason == "sector_too_small"
    assert all(r.ticker.startswith("B") for r in out["rows"] if r.is_champion)


def test_unknown_sector_is_ranked_nowhere_and_never_flagged():
    """A Nasdaq-only name with no GICS row and no override must fail visibly."""
    known = [_row(f"K{i}", cfo=100 - i, ta=200, mc=500, sector="Industrials")
             for i in range(5)]
    orphan = [_row("ORPH", cfo=100, ta=150, mc=400, sector=None)]
    out = cc.rank_universe(known + orphan, top_pct=0.20)
    by_t = {r.ticker: r for r in out["rows"]}

    assert by_t["ORPH"].is_champion is False
    assert by_t["ORPH"].reason == "no_sector"
    assert by_t["ORPH"].sector_rank is None
    assert out["summary"]["excluded_no_sector"] == 1


def test_gics_lookup_prefers_csv_then_overrides():
    sp_rows = [
        {"Symbol": "AOS", "GICS Sector": "Industrials",
         "GICS Sub-Industry": "Building Products"},
        {"Symbol": "BRK.B", "GICS Sector": "Financials",
         "GICS Sub-Industry": "Multi-Sector Holdings"},
    ]
    lookup = cc._gics_lookup(sp_rows)
    assert lookup["AOS"] == ("Industrials", "Building Products")
    # dotted class shares are normalised the same way the universe keys are
    assert lookup[cc._norm("BRK.B")][0] == "Financials"
    # Nasdaq-only names are not in the CSV but are covered by the override table
    assert "MELI" in cc.GICS_OVERRIDES
    assert cc.GICS_OVERRIDES["MELI"] == "Consumer Discretionary"


# ── Batch pipeline robustness ───────────────────────────────────────────────────

_SYNTH_UNIVERSE = {
    "as_of": "2026-06-26",
    "constituents": [
        {"ticker": "AAA", "name": "Alpha", "cik": 111, "exchange": "NYSE",
         "indices": ["sp500"], "gics_sector": "Industrials"},
        {"ticker": "BBB", "name": "Beta", "cik": 222, "exchange": "Nasdaq",
         "indices": ["sp500"], "gics_sector": "Industrials"},
        {"ticker": "CCC", "name": "Gamma", "cik": 333, "exchange": "NYSE",
         "indices": ["dow30"], "gics_sector": "Industrials"},
    ],
}


def test_partial_failure_does_not_abort_run(monkeypatch):
    monkeypatch.setattr(cc, "load_universe", lambda: _SYNTH_UNIVERSE)
    monkeypatch.setattr(cc, "_install_cik_cache", lambda u: None)

    def fake_fetch_one(item, max_cache_age_days):
        if item["ticker"] == "BBB":
            raise TimeoutError("simulated delisted / network timeout")
        return {"ticker": item["ticker"], "fiscal_year": 2025, "sic": 7372,
                "cfo": 100.0, "total_assets": 200.0, "shares": 1e6,
                "price": 50.0, "market_cap": 500.0, "from_cache": False}

    monkeypatch.setattr(cc, "_fetch_one", fake_fetch_one)

    res = cc.compute_champions(concurrency=2)
    by_t = {r.ticker: r for r in res["rows"]}
    # the run completed for all three despite BBB blowing up
    assert len(res["rows"]) == 3
    assert by_t["BBB"].status == "failed"
    assert "TimeoutError" in by_t["BBB"].reason
    # the two survivors were still ranked
    assert res["summary"]["failed"] == 1
    assert res["summary"]["ranked"] == 2
    assert res["summary"]["failures"] == [{"ticker": "BBB", "reason": by_t["BBB"].reason}]


def test_disk_cache_prevents_refetch(monkeypatch, tmp_path):
    # redirect the cache to a temp dir
    monkeypatch.setattr(cc, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(cc, "_cache_path",
                        lambda t: str(tmp_path / f"{cc._norm(t)}.json"))

    calls = {"facts": 0, "price": 0, "submissions": 0}

    def fake_facts(cik):
        calls["facts"] += 1
        return {"_cik": cik}  # opaque; _extract_inputs is stubbed below

    monkeypatch.setattr(cc.gather_data, "fetch_company_facts", fake_facts)
    monkeypatch.setattr(cc, "_extract_inputs", lambda facts: (2025, 100.0, 200.0, 1e6))
    monkeypatch.setattr(cc, "_fetch_price", lambda t: (calls.__setitem__("price", calls["price"] + 1), 50.0)[1])
    monkeypatch.setattr(cc.gather_data, "fetch_company_submissions",
                        lambda cik: (calls.__setitem__("submissions", calls["submissions"] + 1), {"sic": "7372"})[1])

    item = {"ticker": "ZZZ", "cik": 999}
    first = cc._fetch_one(item, max_cache_age_days=30)
    assert first["from_cache"] is False
    assert first["cfo"] == 100 and first["total_assets"] == 200
    assert first["market_cap"] == 50.0  # 50 * 1e6 / 1e6
    assert calls["facts"] == 1

    # second call within max age → served from cache, zero new fetches
    second = cc._fetch_one(item, max_cache_age_days=30)
    assert second["from_cache"] is True
    assert calls["facts"] == 1
    assert calls["price"] == 1

    # an expired cache (max age 0) forces a re-fetch
    third = cc._fetch_one(item, max_cache_age_days=0)
    assert third["from_cache"] is False
    assert calls["facts"] == 2


def test_universe_snapshot_is_well_formed():
    """The checked-in snapshot exists and has the expected shape."""
    uni = cc.load_universe()
    assert uni["count"] == len(uni["constituents"]) > 400
    sample = uni["constituents"][0]
    assert {"ticker", "name", "cik", "exchange", "indices"} <= set(sample)
    # every constituent belongs to at least one index and has a CIK
    assert all(c["indices"] for c in uni["constituents"])
    assert all(isinstance(c["cik"], int) for c in uni["constituents"])
    # snapshot records an as-of date
    json.dumps(uni)  # serialisable
    assert uni["as_of"]


# ── Index constituent parsing ──────────────────────────────────────────────────

_VALID = {cc._norm(t) for t in ("AAPL", "MSFT", "WMT", "MMM", "GS")}


def test_parse_index_tickers_tolerates_tag_attributes():
    """The original parser required a bare <tr>, so it silently matched nothing
    once Wikipedia started emitting <tr class=...>. It then fell back to
    scanning the whole page and returned a handful of stray tickers instead of
    an error — which shrank the universe by 14 names."""
    html = """
      <table class="wikitable sortable"><tbody>
        <tr class="hdr"><th scope="col">Symbol</th><th>Company</th></tr>
        <tr class="r1"><td><a href="/x">MMM</a></td><td>3M</td></tr>
        <tr class="r2"><td><a href="/y">GS</a></td><td>Goldman Sachs</td></tr>
      </tbody></table>
    """
    assert cc._parse_index_tickers(html, _VALID) == ["GS", "MMM"]


def test_parse_index_tickers_picks_the_richest_table():
    """Pages carry several tables (performance, milestones, constituents). Take
    the one that yields the most recognised tickers, not the first."""
    html = """
      <table><tr><td>1985</td><td>132.29</td></tr></table>
      <table><tr><td>AAPL</td></tr><tr><td>MSFT</td></tr><tr><td>WMT</td></tr></table>
    """
    assert cc._parse_index_tickers(html, _VALID) == ["AAPL", "MSFT", "WMT"]


def test_parse_index_tickers_ignores_unrecognised_symbols():
    """Only symbols the SEC ticker file knows survive, so prose and footnote
    markers can't masquerade as constituents."""
    html = "<table><tr><td>AAPL</td></tr><tr><td>ZZZZ</td></tr><tr><td>N/A</td></tr></table>"
    assert cc._parse_index_tickers(html, _VALID) == ["AAPL"]


def test_parse_index_tickers_reads_a_linked_symbol_cell():
    """stockanalysis.com wraps the symbol in an anchor and pads the cell with
    empty comment nodes; the text still has to come out clean."""
    html = ('<table><tr><td class="n">12</td>'
            '<td class="sym"><!----><a href="/stocks/wmt/">WMT</a><!----></td></tr></table>')
    assert cc._parse_index_tickers(html, _VALID) == ["WMT"]


def test_parse_index_tickers_empty_when_no_table_matches():
    """No recognisable table → empty, which the constituent-count floor in
    refresh_universe turns into a loud failure rather than a silent shrink."""
    assert cc._parse_index_tickers("<p>no tables here</p>", _VALID) == []


# ── Wikipedia constituent tables (S&P 400 / 600) ──────────────────────────────

_SP400_LIKE = """
  <table class="wikitable sortable"><tbody>
    <tr><th>Symbol</th><th>Security</th><th>GICS Sector</th>
        <th>GICS Sub-Industry</th><th>Headquarters Location</th></tr>
    <tr><td><a href="/x">AA</a></td><td>Alcoa</td><td>Materials</td>
        <td>Aluminum</td><td>Pittsburgh, Pennsylvania</td></tr>
    <tr><td><a href="/y">YETI</a></td><td>Yeti Holdings</td>
        <td>Consumer Discretionary</td><td>Leisure Products</td>
        <td>Austin, Texas</td></tr>
  </tbody></table>
"""


def test_parse_wiki_constituents_returns_symbol_and_sector():
    """The 400 and 600 lists are the only source for their members' sectors —
    the S&P 500 CSV that feeds every other name does not contain them."""
    assert cc._parse_wiki_constituents(_SP400_LIKE) == [
        ("AA", "Materials", "Aluminum"),
        ("YETI", "Consumer Discretionary", "Leisure Products"),
    ]


def test_parse_wiki_constituents_skips_the_changes_table():
    """The S&P 600 article's "selected changes" table has almost as many rows
    as the constituents table and lists names that have LEFT the index. Picking
    the biggest table would put delisted companies in the universe, so the
    header is what identifies the right one."""
    changes = """
      <table><tbody>
        <tr><th>Date</th><th>Added</th><th>Removed</th><th>Reason</th></tr>
        <tr><td>2026-01-02</td><td>MSFT</td><td>WMT</td><td>Market cap</td></tr>
        <tr><td>2026-01-03</td><td>GS</td><td>MMM</td><td>Acquired</td></tr>
        <tr><td>2026-01-04</td><td>AAPL</td><td>AA</td><td>Merger</td></tr>
      </tbody></table>
    """
    assert cc._parse_wiki_constituents(changes + _SP400_LIKE) == [
        ("AA", "Materials", "Aluminum"),
        ("YETI", "Consumer Discretionary", "Leisure Products"),
    ]


def test_parse_wiki_constituents_empty_without_gics_columns():
    """A restructured article yields nothing rather than a partial list; the
    constituent-count floor in refresh_universe then refuses to write."""
    html = "<table><tr><th>Symbol</th><th>Company</th></tr><tr><td>AA</td><td>Alcoa</td></tr></table>"
    assert cc._parse_wiki_constituents(html) == []
