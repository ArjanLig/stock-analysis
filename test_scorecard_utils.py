"""Unit tests for scorecard_utils display helpers."""

import pytest

from scorecard_utils import prettify_company_name


@pytest.mark.parametrize("raw,expected", [
    # EDGAR all-caps issuer names get title-cased
    ("TAIWAN SEMICONDUCTOR MANUFACTURING CO LTD", "Taiwan Semiconductor Manufacturing Co Ltd"),
    ("ABBOTT LABORATORIES", "Abbott Laboratories"),
    ("ADOBE INC.", "Adobe Inc."),
    ("COMCAST CORP", "Comcast Corp"),
    ("HOME DEPOT, INC.", "Home Depot, Inc."),
    ("PROCTER & GAMBLE Co", "Procter & Gamble Co"),
    ("BANK OF AMERICA CORP", "Bank of America Corp"),  # connector lowercased
    # Brand acronyms / internal capitals preserved
    ("NVIDIA CORP", "NVIDIA Corp"),
    ("AT&T INC.", "AT&T Inc."),
    ("MERCADOLIBRE INC", "MercadoLibre Inc"),
    # Already nicely-cased names are left untouched
    ("AbbVie Inc.", "AbbVie Inc."),
    ("Amazon.com Inc", "Amazon.com Inc"),
    ("McDonald's Corporation", "McDonald's Corporation"),
    ("Eli Lilly and Company", "Eli Lilly and Company"),
    ("PepsiCo Inc", "PepsiCo Inc"),
])
def test_prettify_company_name(raw, expected):
    assert prettify_company_name(raw) == expected


@pytest.mark.parametrize("val", [None, "", "   ", 123, {"x": 1}])
def test_prettify_company_name_handles_non_strings(val):
    # Should never raise; returns the input unchanged for non-text values.
    assert prettify_company_name(val) == val


from scorecard_utils import compute_roce_metric


def _fund(oi, ta, cl, ni=None, eq=None):
    n = len(oi)
    return {
        "years": list(range(2016, 2016 + n)),
        "operating_income": oi, "total_assets": ta, "current_liabilities": cl,
        "net_income": ni or [None] * n, "total_equity": eq or [None] * n,
    }


def test_roce_uses_ta_minus_cl_no_goodwill_subtraction():
    # CE = TA − CL only; goodwill/cash irrelevant. EBIT 20 / (100−20) = 25%.
    metric, val = compute_roce_metric(_fund([20.0], [100.0], [20.0]))
    assert metric == "ROCE"
    assert round(val, 1) == 25.0


def test_genuine_float_auto_falls_back_to_roe():
    # Current liabilities eat 80% of assets → CE/TA 0.20 < 0.25 → ROE.
    f = _fund([10.0], [100.0], [80.0], ni=[15.0], eq=[50.0])
    metric, val = compute_roce_metric(f)
    assert metric == "ROE"
    assert round(val, 1) == 30.0  # 15 / 50


def test_acquisition_heavy_stays_roce():
    # Big asset base, modest CL → CE/TA 0.70 ≥ 0.25 → ROCE (no goodwill drag).
    metric, _ = compute_roce_metric(_fund([12.0], [100.0], [30.0]))
    assert metric == "ROCE"


def test_manual_override_forces_roe_on_non_float():
    # Auto would say ROCE, but the float flag forces ROE.
    f = _fund([12.0], [100.0], [30.0], ni=[8.0], eq=[40.0])
    metric, val = compute_roce_metric(f, {"roce_metric_override": "ROE"})
    assert metric == "ROE"
    assert round(val, 1) == 20.0  # 8 / 40


def test_manual_override_forces_roce_on_float():
    # Auto would say ROE (float), but override pins ROCE.
    f = _fund([10.0], [100.0], [80.0], ni=[15.0], eq=[50.0])
    metric, val = compute_roce_metric(f, {"roce_metric_override": "ROCE"})
    assert metric == "ROCE"
    assert round(val, 1) == 50.0  # 10 / (100−80)


from scorecard_utils import excess_liquidity, capital_employed, roce_for_year


def _fund_liq(cash=0, sti=0, lti=0, debt=0, opl=0, fnl=0, ta=0, cl=0, oi=0):
    """Single-year fund for liquidity/CE helper tests."""
    return {
        "years": [2023],
        "cash": [cash], "short_term_investments": [sti], "long_term_investments": [lti],
        "total_debt": [debt], "operating_lease_liabilities": [opl],
        "finance_lease_liabilities": [fnl],
        "total_assets": [ta], "current_liabilities": [cl], "operating_income": [oi],
    }


def test_excess_liquidity_net_cash_counts_all_marketables():
    # liquid = 100+50+30 = 180; debt = 20+10+5 = 35; excess = 145
    f = _fund_liq(cash=100, sti=50, lti=30, debt=20, opl=10, fnl=5)
    assert excess_liquidity(f, 0) == 145


def test_excess_liquidity_floors_at_zero_when_net_debt():
    # liquid = 30; debt = 100 → negative → floored to 0
    f = _fund_liq(cash=30, debt=100)
    assert excess_liquidity(f, 0) == 0


def test_excess_liquidity_lt_investments_included():
    # regression: LT investments are the bulk of the surplus (VEEV/PANW-like)
    f = _fund_liq(cash=10, sti=0, lti=200, debt=0)
    assert excess_liquidity(f, 0) == 210


def test_excess_liquidity_missing_series_default_zero():
    f = {"years": [2023], "cash": [40], "total_assets": [100],
         "current_liabilities": [20], "operating_income": [10]}  # no investments/debt keys
    assert excess_liquidity(f, 0) == 40


def test_capital_employed_strips_excess_liquidity():
    # TA−CL = 80; excess = 145 (from net cash) → CE = 80−145 = −65
    f = _fund_liq(cash=100, sti=50, lti=30, debt=20, opl=10, fnl=5, ta=100, cl=20)
    assert capital_employed(f, 0) == -65


def test_roce_for_year_normal():
    # excess 0 (net debt), CE = 100−20 = 80, oi 20 → 25%
    f = _fund_liq(cash=0, debt=100, ta=100, cl=20, oi=20)
    pct, capped = roce_for_year(f, 0)
    assert round(pct, 1) == 25.0 and capped is False


def test_roce_for_year_ce_negative_caps_and_passes():
    # capital-light: CE ≤ 0 → ceiling, capped True, year kept
    f = _fund_liq(cash=100, lti=100, ta=50, cl=10, oi=30)  # excess 200 > TA−CL 40
    pct, capped = roce_for_year(f, 0)
    assert pct == 100.0 and capped is True


def test_roce_for_year_clamps_above_ceiling():
    # tiny positive CE → huge ROCE → clamped to ceiling
    f = _fund_liq(cash=0, debt=100, ta=41, cl=40, oi=50)  # CE = 1, oi/ce = 5000%
    pct, capped = roce_for_year(f, 0)
    assert pct == 100.0 and capped is True


def test_roce_for_year_missing_inputs_returns_none():
    f = {"years": [2023], "total_assets": [100], "current_liabilities": [20]}  # no oi
    assert roce_for_year(f, 0) == (None, False)
