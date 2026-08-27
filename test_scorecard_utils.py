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


from scorecard_utils import (non_operating_cash, capital_employed,
                             roce_for_year)


def _fund_liq(cash=0, sti=0, lti=0, debt=0, opl=0, fnl=0, ta=0, cl=0, oi=0):
    """Single-year fund for liquidity/CE helper tests."""
    return {
        "years": [2023],
        "cash": [cash], "short_term_investments": [sti], "long_term_investments": [lti],
        "total_debt": [debt], "operating_lease_liabilities": [opl],
        "finance_lease_liabilities": [fnl],
        "total_assets": [ta], "current_liabilities": [cl], "operating_income": [oi],
    }


def test_non_operating_cash_counts_short_term_investments():
    # MSFT's shape: the war chest is mostly in short-term investments, so
    # stripping only "cash" would leave three quarters of it in the denominator.
    f = _fund_liq(cash=100, sti=50)
    assert non_operating_cash(f, 0) == 150


def test_non_operating_cash_leaves_long_term_investments_in():
    # A long-term investment is capital deliberately committed, not money
    # between uses, and this metric asks what the committed capital earns.
    f = _fund_liq(cash=10, lti=200)
    assert non_operating_cash(f, 0) == 10


def test_non_operating_cash_ignores_debt_and_leases():
    # No netting: an earlier basis subtracted max(0, marketables − debt), which
    # made the answer depend on the liability side. MSFT's finance leases
    # exceeded its marketables so nothing came off; Hermès, with almost no debt,
    # had its whole cash pile stripped. Same formula, two behaviours.
    f = _fund_liq(cash=100, sti=50, debt=9999, opl=9999, fnl=9999)
    assert non_operating_cash(f, 0) == 150


def test_non_operating_cash_missing_series_default_zero():
    f = {"years": [2023], "cash": [40], "total_assets": [100],
         "current_liabilities": [20], "operating_income": [10]}  # no STI key
    assert non_operating_cash(f, 0) == 40


def test_capital_employed_strips_cash_and_short_term_investments():
    # TA−CL = 80; cash+STI = 150 → CE = 80−150 = −70
    f = _fund_liq(cash=100, sti=50, lti=30, debt=20, opl=10, fnl=5, ta=100, cl=20)
    assert capital_employed(f, 0) == -70


def test_capital_employed_keeps_goodwill_in():
    # Stripping goodwill is what blew this up in June 2026: an acquisitive name
    # carries most of its assets there and the denominator collapsed.
    f = _fund_liq(ta=100, cl=20, oi=12)
    f["goodwill"] = [60]
    assert capital_employed(f, 0) == 80
    assert round(roce_for_year(f, 0)[0], 1) == 15.0


def test_roce_for_year_normal():
    # no cash, CE = 100−20 = 80, oi 20 → 25%
    f = _fund_liq(cash=0, debt=100, ta=100, cl=20, oi=20)
    pct, capped = roce_for_year(f, 0)
    assert round(pct, 1) == 25.0 and capped is False


def test_roce_for_year_ce_negative_caps_and_passes():
    # capital-light: CE ≤ 0 → ceiling, capped True, year kept
    f = _fund_liq(cash=100, sti=100, ta=50, cl=10, oi=30)  # cash 200 > TA−CL 40
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


def test_compute_roce_strips_cash_raises_roce():
    # Two-year cash-rich name. TA−CL 160, cash 100 → CE 60.
    # EBIT 30 → 50%, against 18.75% with the cash left in.
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

from scorecard_utils import ROCE_WINDOW_YEARS, window_start

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
    # Kept on the fixture although nothing reads them any more: the denominator
    # no longer touches debt or leases, and a test that still passes with them
    # present is the proof of it.
    "operating_lease_liabilities": [None, None, None, None, 378.0, 306.0, 288.0,
                                    304.0, 315.0, 326.0, 384.0],
    "finance_lease_liabilities": [None] * 11,
}


def test_bke_latest_year_strips_cash_regardless_of_its_leases():
    # FY2026: CE = 991 − 237 − 249.5 − 25 = 479.5, so 261/479.5 = 54.4%.
    # The 384 of lease liabilities used to cancel the cash out entirely and
    # leave CE at 754 for 34.6%; the liability side no longer enters into it.
    i = len(_BKE_YEARS) - 1
    pct, capped = roce_for_year(_BKE, i)
    assert round(capital_employed(_BKE, i), 1) == 479.5
    assert round(pct, 1) == 54.4
    assert not capped


def test_bke_average_ignores_history_beyond_the_window():
    # 11 years of input, 10-year answer: FY2016 must not reach the mean.
    metric, val = compute_roce_metric(_BKE)
    assert metric == "ROCE"
    assert round(val, 2) == 64.40

    eleven = [roce_for_year(_BKE, i)[0] for i in range(len(_BKE_YEARS))]
    assert round(sum(eleven) / len(eleven), 2) == 66.35


def test_bke_leases_do_not_enter_the_denominator():
    # This filer reports no lease liability before FY2020 and a large one after,
    # which used to move its ROCE by 42 points across that boundary — the
    # accounting standard arriving, not the business changing. Now FY2019 and
    # FY2020 differ only by their own balance sheets.
    assert round(roce_for_year(_BKE, 3)[0], 1) == 55.8   # FY2019, no leases on file
    assert round(roce_for_year(_BKE, 4)[0], 1) == 28.4   # FY2020, 378 of leases


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


# ---------------------------------------------------------------------------
# The 2026-08-27 basis, anchored on real filings.
#
#   capital employed = (Total Assets − Current Liabilities) − cash
#                      − short-term investments
#
# Goodwill stays in. Debt and leases play no part. Decision and rationale:
# portfolio-vault/Concepts/LazyTheta-Lens-Mechanica.md, "HERZIENING 2026-08-27".
# ---------------------------------------------------------------------------

def _year(oi, ta, cl, cash, sti=0.0, **extra):
    f = {"years": [2026], "operating_income": [oi], "total_assets": [ta],
         "current_liabilities": [cl], "cash": [cash],
         "short_term_investments": [sti]}
    for k, v in extra.items():
        f[k] = [v]
    return f


def test_msft_fy2026_anchor():
    # The war chest is mostly short-term investments: stripping cash alone
    # would leave $55.9bn of it in the denominator and read 28.9%.
    f = _year(oi=155237, ta=758376, cl=168825, cash=20935, sti=55908)
    assert capital_employed(f, 0) == 512708
    assert round(roce_for_year(f, 0)[0], 1) == 30.3


def test_msft_leases_no_longer_cancel_its_cash():
    # MSFT's finance leases exceeded its marketables, so the old
    # max(0, marketables − debt) came to nothing and the whole cash pile stayed
    # in capital employed at 26.3%. The liability side is now irrelevant.
    f = _year(oi=155237, ta=758376, cl=168825, cash=20935, sti=55908,
              total_debt=40294, operating_lease_liabilities=16532,
              finance_lease_liabilities=66594, long_term_investments=36348)
    assert capital_employed(f, 0) == 512708
    assert round(roce_for_year(f, 0)[0], 1) == 30.3


def test_rms_pa_fy2025_anchor():
    # Hermès, the other side of the same old formula: almost no debt, so its
    # entire cash pile came off and then debt was added back, giving 59.6%.
    f = _year(oi=6696, ta=24322, cl=3186, cash=12239, sti=0.0,
              total_debt=34, operating_lease_liabilities=2312, goodwill=180)
    assert capital_employed(f, 0) == 8897
    assert round(roce_for_year(f, 0)[0], 1) == 75.3


def test_the_two_paths_that_used_to_disagree_now_share_one_rule():
    # Same function, same inputs, one behaviour. What made MSFT and Hermès look
    # like separate code paths was max(0, marketables − debt) clamping for one
    # and not the other — not a second implementation.
    msft = _year(oi=155237, ta=758376, cl=168825, cash=20935, sti=55908,
                 total_debt=40294, finance_lease_liabilities=66594)
    rms = _year(oi=6696, ta=24322, cl=3186, cash=12239,
                total_debt=34, operating_lease_liabilities=2312)
    for f in (msft, rms):
        ta, cl = f["total_assets"][0], f["current_liabilities"][0]
        cash, sti = f["cash"][0], f["short_term_investments"][0]
        assert capital_employed(f, 0) == ta - cl - cash - sti


class TestFloatDetectorStaysOnTheCashInclusiveBasis:
    """The value strips cash; the float test must not.

    The float test asks whether current liabilities fund so much of the balance
    sheet that there is barely any capital employed to speak of — a bank, an
    insurer, a settlement network. Cash is part of that balance sheet, so it
    belongs in that ratio. Run the test on the cash-stripped denominator and
    every cash-rich name falls under the 25% threshold and is relabelled a float
    business, which is the misclassification the 2026-06-16 fix ended.
    """

    def test_a_cash_rich_name_does_not_flip_to_roe(self):
        # (TA−CL)/TA = 160/200 = 0.80, far above the threshold. But the
        # cash-stripped CE is 10, and 10/200 = 0.05 would trip it.
        f = {
            "years": [2026], "operating_income": [30.0],
            "total_assets": [200.0], "current_liabilities": [40.0],
            "cash": [140.0], "short_term_investments": [10.0],
            "net_income": [25.0], "total_equity": [50.0],
        }
        metric, val = compute_roce_metric(f)
        assert metric == "ROCE"
        # CE falls to 10 and 30/10 would be 300%, which the ceiling holds at
        # 100. Still ROCE, and still a pass — which is the point: a cash pile
        # must not turn a quality name into a float business.
        assert val == 100.0

    def test_a_genuine_float_business_still_falls_back(self):
        # Current liabilities fund 80% of the assets: (TA−CL)/TA = 0.20.
        f = {
            "years": [2026], "operating_income": [10.0],
            "total_assets": [100.0], "current_liabilities": [80.0],
            "cash": [0.0], "short_term_investments": [0.0],
            "net_income": [15.0], "total_equity": [50.0],
        }
        metric, val = compute_roce_metric(f)
        assert metric == "ROE"
        assert round(val, 1) == 30.0

    def test_cash_alone_never_decides_the_metric(self):
        # Same business twice, once with a pile of cash. The metric chosen must
        # not change; only the ROCE value moves.
        base = {
            "years": [2026], "operating_income": [30.0],
            "total_assets": [200.0], "current_liabilities": [40.0],
            "net_income": [25.0], "total_equity": [50.0],
            "cash": [0.0], "short_term_investments": [0.0],
        }
        rich = dict(base, cash=[120.0])
        assert compute_roce_metric(base)[0] == compute_roce_metric(rich)[0] == "ROCE"
        assert compute_roce_metric(rich)[1] > compute_roce_metric(base)[1]


def test_a_single_year_keeps_whatever_it_actually_was():
    # No floor per year. CE = 100 − 20 − 79 = 1, EBIT −50 → −5000%, and that is
    # what the series says. This is what the chart draws and the record of what
    # happened; the clamp belongs on the headline.
    f = _fund_liq(cash=79, ta=100, cl=20, oi=-50)
    pct, capped = roce_for_year(f, 0)
    assert round(pct) == -5000 and capped is False


def test_an_ordinary_bad_year_is_untouched():
    # CE = 80, EBIT −20 → −25%, a real number that stays one.
    f = _fund_liq(cash=0, ta=100, cl=20, oi=-20)
    pct, capped = roce_for_year(f, 0)
    assert round(pct, 1) == -25.0 and capped is False


def test_the_average_is_floored_but_the_years_are_not():
    # Two years: one ordinary (−25%), one where the cash strip leaves a sliver
    # of capital (−5000%). The mean of those is −2512.5, which is the figure a
    # person reads, so it is held at −100 — while the series keeps both.
    f = {
        "years": [2025, 2026],
        "operating_income": [-20.0, -50.0],
        "total_assets": [100.0, 100.0],
        "current_liabilities": [20.0, 20.0],
        "cash": [0.0, 79.0], "short_term_investments": [0.0, 0.0],
    }
    per_year = [roce_for_year(f, i)[0] for i in range(2)]
    assert round(per_year[0], 1) == -25.0
    assert round(per_year[1]) == -5000
    metric, val = compute_roce_metric(f)
    assert metric == "ROCE"
    assert val == -100.0


def test_a_deeply_negative_roe_is_not_floored():
    # A loss against real equity has no shrinking denominator behind it to
    # distrust, so it is reported as it is.
    f = {
        "years": [2026], "operating_income": [1.0],
        "total_assets": [100.0], "current_liabilities": [85.0],
        "cash": [0.0], "short_term_investments": [0.0],
        "net_income": [-500.0], "total_equity": [100.0],
    }
    metric, val = compute_roce_metric(f)
    assert metric == "ROE"
    assert round(val, 1) == -500.0
