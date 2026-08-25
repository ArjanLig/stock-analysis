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


def test_compute_roce_strips_excess_liquidity_raises_roce():
    # Two-year cash-rich name. Old: EBIT/(TA−CL). New: EBIT/(TA−CL−excess).
    # y: TA 200, CL 40 → TA−CL 160. excess = cash 100 (net cash, no debt).
    # New CE = 60. EBIT 30 → 50% (old was 30/160 = 18.75%).
    f = {
        "years": [2022, 2023],
        "operating_income": [30.0, 30.0],
        "total_assets": [200.0, 200.0],
        "current_liabilities": [40.0, 40.0],
        "cash": [100.0, 100.0],
        "short_term_investments": [0.0, 0.0], "long_term_investments": [0.0, 0.0],
        "total_debt": [0.0, 0.0],
        "operating_lease_liabilities": [0.0, 0.0], "finance_lease_liabilities": [0.0, 0.0],
    }
    metric, val = compute_roce_metric(f)
    assert metric == "ROCE"
    assert round(val, 1) == 50.0


def test_compute_roce_cash_rich_name_does_not_flip_to_roe():
    # Float-test denominator stays (TA−CL)/TA = 160/200 = 0.80 ≥ 0.25 → ROCE,
    # even though the excess strip makes the ROCE-value CE small.
    f = {
        "years": [2023],
        "operating_income": [30.0], "total_assets": [200.0], "current_liabilities": [40.0],
        "cash": [150.0], "short_term_investments": [0.0], "long_term_investments": [0.0],
        "total_debt": [0.0], "operating_lease_liabilities": [0.0],
        "finance_lease_liabilities": [0.0],
        "net_income": [25.0], "total_equity": [50.0],
    }
    metric, _ = compute_roce_metric(f)
    assert metric == "ROCE"  # NOT flipped to ROE


def test_compute_roce_keeps_ce_negative_year_at_ceiling():
    # One capital-light year (CE≤0 → 100%) + one normal year (25%) → mean 62.5%.
    f = {
        "years": [2022, 2023],
        "operating_income": [30.0, 20.0],
        "total_assets": [50.0, 100.0],
        "current_liabilities": [10.0, 20.0],
        "cash": [200.0, 0.0],  # yr0 excess 200 > TA−CL 40 → CE≤0 → 100%
        "short_term_investments": [0.0, 0.0], "long_term_investments": [0.0, 0.0],
        "total_debt": [0.0, 100.0],  # yr1 net debt → no strip → 20/80 = 25%
        "operating_lease_liabilities": [0.0, 0.0], "finance_lease_liabilities": [0.0, 0.0],
    }
    metric, val = compute_roce_metric(f)
    assert metric == "ROCE"
    assert round(val, 1) == 62.5


# ---------------------------------------------------------------------------
# BKE (Buckle Inc) — the regression that produced two different ROCEs for one
# ticker. Real EDGAR values, FY2016–FY2026, $M, with the cash overrides this
# filer needs (EDGAR tags neither cash nor debt for it).
#
# Cash-rich and goodwill-free, so the three candidate formulas fan out widely
# on the latest year: 34.6% canonical, 51.7% on the abandoned pre-2026-06-16
# ex-cash basis — 17pp apart. What the detail page actually showed, 51.7%, was
# neither: it was the correct formula averaged over 11 years instead of 10,
# landing by coincidence on the same number as the old formula's latest year.
# ---------------------------------------------------------------------------

from scorecard_utils import (ROCE_WINDOW_YEARS, window_start,
                             first_lease_reporting_year)

_BKE_YEARS = [2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026]
_BKE = {
    "years": _BKE_YEARS,
    "operating_income": [230.0, 153.0, 134.0, 121.0, 131.0, 168.0, 335.0, 328.0, 271.0, 241.0, 261.0],
    "net_income": [147.0, 98.0, 90.0, 96.0, 104.0, 130.0, 255.0, 255.0, 220.0, 195.0, 210.0],
    "total_equity": [413.0, 431.0, 391.0, 394.0, 389.0, 397.0, 313.0, 376.0, 413.0, 424.0, 425.0],
    "total_assets": [573.0, 580.0, 538.0, 527.0, 868.0, 846.0, 781.0, 838.0, 890.0, 913.0, 991.0],
    "current_liabilities": [108.0, 99.0, 98.0, 90.0, 173.0, 206.0, 249.0, 226.0, 221.0, 214.0, 237.0],
    "cash": [161.0, 197.0, 165.0, 168.0, 221.0, 318.8, 254.0, 252.1, 268.2, 266.9, 249.5],
    "short_term_investments": [36.0, 50.0, 51.0, 52.0, 13.0, 3.0, 13.0, 21.0, 22.0, 24.0, 25.0],
    "long_term_investments": [34.0, 18.0, 21.0, 19.0, 16.0, 18.0, 19.0, 21.0, 25.0, 28.0, 32.0],
    "total_debt": [0.0] * 11,
    "goodwill": [None] * 11,
    # ASC 842 only puts leases on the balance sheet from FY2020 for this filer.
    "operating_lease_liabilities": [None, None, None, None, 378.0, 306.0, 288.0,
                                    304.0, 315.0, 326.0, 384.0],
    "finance_lease_liabilities": [None] * 11,
}


def test_bke_latest_year_is_the_canonical_ta_minus_cl_basis():
    # FY2026 marketables 249.5+25+32 = 306.5 sit under the 384 lease liability,
    # so nothing is stripped and CE is plain TA−CL: 261/(991−237) = 34.6%.
    # The abandoned ex-cash basis would say 261/(991−237−249.5) = 51.7%.
    pct, capped = roce_for_year(_BKE, len(_BKE_YEARS) - 1)
    assert round(pct, 1) == 34.6
    assert not capped
    assert round(capital_employed(_BKE, len(_BKE_YEARS) - 1), 1) == 754.0
    assert excess_liquidity(_BKE, len(_BKE_YEARS) - 1) == 0.0


def test_bke_average_ignores_history_beyond_the_window():
    # 11 years of input, 10-year answer: FY2016 must not reach the mean.
    metric, val = compute_roce_metric(_BKE)
    assert metric == "ROCE"
    assert round(val, 2) == 36.27

    eleven = [roce_for_year(_BKE, i)[0] for i in range(len(_BKE_YEARS))]
    assert round(sum(eleven) / len(eleven), 2) == 37.47


def test_bke_pre_asc842_years_keep_their_cash_in_capital():
    # FY2016-FY2019 predate this filer's first reported lease liability, so the
    # debt side of the strip is missing its largest component. Stripping anyway
    # put FY2019 at 61.1% against FY2020's 18.8% — a 42-point step that is ASC
    # 842 landing, not the business changing. Both years now sit on plain TA−CL.
    assert first_lease_reporting_year(_BKE) == 4  # FY2020
    for i in range(4):
        assert excess_liquidity(_BKE, i) == 0.0
    assert round(roce_for_year(_BKE, 3)[0], 1) == 27.7  # FY2019, was 61.1
    assert round(roce_for_year(_BKE, 4)[0], 1) == 18.8  # FY2020, unchanged


def test_explicit_zero_lease_liability_counts_as_reported():
    # A filer stating it has no leases is not a filer from before the standard
    # existed: 0.0 is data, None is absence. The strip must still run.
    f = {
        "years": [2023],
        "operating_income": [30.0], "total_assets": [200.0],
        "current_liabilities": [40.0], "cash": [100.0],
        "short_term_investments": [0.0], "long_term_investments": [0.0],
        "total_debt": [0.0],
        "operating_lease_liabilities": [0.0], "finance_lease_liabilities": [0.0],
    }
    assert first_lease_reporting_year(f) == 0
    assert excess_liquidity(f, 0) == 100.0
    assert round(roce_for_year(f, 0)[0], 1) == 50.0  # 30 / (200−40−100)


def test_bke_ten_year_input_gives_the_same_answer_as_eleven():
    # The window is the helper's, not the caller's: feeding it exactly ten
    # years must not change the answer feeding it eleven gives.
    ten = {k: (v[1:] if isinstance(v, list) else v) for k, v in _BKE.items()}
    assert len(ten["years"]) == ROCE_WINDOW_YEARS
    assert window_start(ten) == 0
    assert window_start(_BKE) == 1
    assert compute_roce_metric(ten) == compute_roce_metric(_BKE)


def test_bke_cash_rich_and_goodwill_free_stays_on_roce():
    # Float test runs on the unadjusted (TA−CL)/TA, which averages well above
    # the 25% threshold, so the cash pile must not flip this name to ROE.
    metric, _ = compute_roce_metric(_BKE)
    assert metric == "ROCE"
    assert compute_roce_metric(_BKE, {"roce_metric_override": "ROE"})[0] == "ROE"
