"""Tests for gather_data's secondary income-statement fallback.

Some filers (e.g. APA Corp after its 2021 holdco reorg) expose no machine-
readable revenue line in EDGAR — revenue is tagged only dimensionally and the
flat companyfacts feed drops it. parse_financials then falls back to a secondary
income-statement source (yfinance) while keeping balance-sheet / cash-flow items
from EDGAR. These tests cover that path without any network call.
"""
import sys
import types

import pandas as pd

import gather_data as g


def _install_fake_yfinance(monkeypatch, df):
    mod = types.ModuleType("yfinance")

    class _Ticker:
        def __init__(self, _t):
            pass

        @property
        def income_stmt(self):
            return df

    mod.Ticker = _Ticker
    monkeypatch.setitem(sys.modules, "yfinance", mod)


def test_fetch_income_statement_yf_parses_dataframe(monkeypatch):
    df = pd.DataFrame({
        pd.Timestamp("2024-12-31"): {
            "Total Revenue": 9737e6, "Operating Income": 3199e6,
            "Net Income": 804e6, "Tax Provision": 417e6,
            "Pretax Income": 1535e6, "Cost Of Revenue": 5435e6,
        },
        pd.Timestamp("2023-12-31"): {
            "Total Revenue": 8279e6, "Operating Income": 3358e6,
            "Net Income": 2855e6, "Tax Provision": -324e6,
            "Pretax Income": 2883e6, "Cost Of Revenue": 4052e6,
        },
    })
    _install_fake_yfinance(monkeypatch, df)

    out = g._fetch_income_statement_yf("APA", n_years=6)
    assert set(out) == {2023, 2024}
    assert out[2024]["revenue"] == 9737e6
    assert out[2023]["operating_income"] == 3358e6
    assert out[2024]["tax_provision"] == 417e6


def test_fetch_income_statement_yf_none_without_ticker_or_module(monkeypatch):
    assert g._fetch_income_statement_yf(None) is None
    # module missing → None (graceful, e.g. on a stripped-down deploy)
    monkeypatch.setitem(sys.modules, "yfinance", None)
    assert g._fetch_income_statement_yf("APA") is None


def test_parse_financials_uses_yf_when_edgar_has_no_revenue(monkeypatch):
    # EDGAR facts with no revenue tag at all
    facts = {"facts": {"us-gaap": {}, "dei": {}}}
    monkeypatch.setattr(g, "_fetch_income_statement_yf", lambda t, n=6: {
        2023: {"revenue": 8279e6, "operating_income": 3358e6, "net_income": 2855e6,
               "tax_provision": -324e6, "pretax_income": 2883e6, "cost_of_revenue": 4052e6},
        2024: {"revenue": 9737e6, "operating_income": 3199e6, "net_income": 804e6,
               "tax_provision": 417e6, "pretax_income": 1535e6, "cost_of_revenue": 5435e6},
    })
    res = g.parse_financials(facts, n_years=6, ticker="APA")
    assert res["years"] == [2023, 2024]
    assert res["revenue"] == [8279.0, 9737.0]            # raw → millions
    assert res["operating_income"] == [3358.0, 3199.0]
    assert res["net_income"] == [2855.0, 804.0]


def test_parse_financials_raises_when_no_revenue_and_no_fallback(monkeypatch):
    facts = {"facts": {"us-gaap": {}, "dei": {}}}
    monkeypatch.setattr(g, "_fetch_income_statement_yf", lambda t, n=6: None)
    try:
        g.parse_financials(facts, n_years=6, ticker="APA")
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "revenue" in str(e).lower()


# ── resolve_sector_betas: live Damodaran beta wins over the hardcoded snapshot ──

def test_resolve_sector_betas_prefers_live_damodaran_beta(monkeypatch):
    """SIC_TO_SECTOR is a point-in-time snapshot whose betas drift badly
    (Oil/Gas (Integrated) sat at 0.95 against a live 0.27). The SIC → sector
    NAME mapping is still useful, but the beta must come from the live table
    whenever Damodaran still publishes that sector."""
    monkeypatch.setattr(g, "SIC_TO_SECTOR", {1311: ("Oil/Gas (Production and Exploration)", 1.10)})
    monkeypatch.setattr(g, "fetch_sector_betas",
                        lambda: {"Oil/Gas (Production and Exploration)": 0.56})

    out = g.resolve_sector_betas(1311)
    assert out == [("Oil/Gas (Production and Exploration)", 0.56, 1.0)]


def test_resolve_sector_betas_falls_back_when_name_retired(monkeypatch):
    """Some hardcoded names ('Pharma & Drugs', 'Retail (Online)') no longer
    exist in Damodaran's table — keep the hardcoded beta rather than dropping
    to a meaningless market beta."""
    monkeypatch.setattr(g, "SIC_TO_SECTOR", {2834: ("Pharma & Drugs", 0.95)})
    monkeypatch.setattr(g, "fetch_sector_betas", lambda: {"Drugs (Pharmaceutical)": 0.89})

    assert g.resolve_sector_betas(2834) == [("Pharma & Drugs", 0.95, 1.0)]


def test_resolve_sector_betas_falls_back_when_fetch_fails(monkeypatch):
    """A failed Damodaran fetch must not lose the sector — the hardcoded beta
    is the safety net (the deployed host's outbound network is flaky)."""
    monkeypatch.setattr(g, "SIC_TO_SECTOR", {7372: ("Software (System & Application)", 1.23)})
    monkeypatch.setattr(g, "fetch_sector_betas", lambda: {})

    assert g.resolve_sector_betas(7372) == [("Software (System & Application)", 1.23, 1.0)]

    monkeypatch.setattr(g, "fetch_sector_betas", lambda: None)
    assert g.resolve_sector_betas(7372) == [("Software (System & Application)", 1.23, 1.0)]


def test_resolve_sector_betas_fuzzy_matches_description(monkeypatch):
    """SIC not in the table → fall back to a word-overlap match on the SIC
    description against the live table."""
    monkeypatch.setattr(g, "SIC_TO_SECTOR", {})
    monkeypatch.setattr(g, "fetch_sector_betas",
                        lambda: {"Precious Metals": 0.79, "Semiconductor": 1.49})

    assert g.resolve_sector_betas(9999, "Precious Metals Mining") == [("Precious Metals", 0.79, 1.0)]


def test_resolve_sector_betas_last_resort_is_market(monkeypatch):
    """Nothing matches at all → an explicit market placeholder."""
    monkeypatch.setattr(g, "SIC_TO_SECTOR", {})
    monkeypatch.setattr(g, "fetch_sector_betas", lambda: {"Semiconductor": 1.49})

    assert g.resolve_sector_betas(9999, "zzz qqq") == [("Market", 1.0, 1.0)]
