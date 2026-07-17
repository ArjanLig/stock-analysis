# Excess-liquidity correction in the ROCE gate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Strip excess liquidity (cash + ST + LT investments − debt incl. leases) from capital employed in the ROCE gate, so capital-light compounders aren't penalised by a cash-heavy denominator — while leaving the float-business ROE fallback untouched.

**Architecture:** Two shared helpers in `scorecard_utils.py` (`excess_liquidity`, `capital_employed`, and a per-year `roce_for_year` that applies a ceiling cap) become the single source used by both `compute_roce_metric` (the mean) and the `mcp_server.py` per-year loop (latest/rising). `fetch_fundamentals` is extended to carry the two investment series the helpers need.

**Tech Stack:** Python 3.13, `pytest` / `unittest`, existing `scorecard_utils.py`, `mcp_server.py`, `gather_data.py`.

## Global Constraints

- Lint clean before every commit: `python3 -m ruff check .` (ruff.toml).
- Existing suites stay green: `python3 -m pytest test_scorecard_utils.py test_tastytrade_api.py test_ibkr_api.py -v`.
- **Definition (per year i)** — copy verbatim:
  - `liquid[i] = cash[i] + short_term_investments[i] + long_term_investments[i]`
  - `debt[i] = total_debt[i] + operating_lease_liabilities[i] + finance_lease_liabilities[i]`
  - `excess_liquidity[i] = max(0, liquid[i] − debt[i])`
  - `CE[i] = total_assets[i] − current_liabilities[i] − excess_liquidity[i]`
  - Missing/None component → treated as 0.
- **ROCE ceiling = 100.0** (`ROCE_CEILING`). `CE ≤ 0` → per-year ROCE = ceiling, `capped=True`, year KEPT (not dropped). `pct > ceiling` → clamp to ceiling, `capped=True`.
- **Float-test denominator is UNCHANGED**: the ROE-fallback test uses `(TA − CL)/TA` with threshold `FLOAT_CE_TA_THRESHOLD = 0.25`. Only the ROCE *value* uses the excess-liquidity-adjusted CE. Never subtract excess liquidity in the float test.
- Portfolio-wide; no per-config toggle.
- Spec: `docs/superpowers/specs/2026-07-17-roce-excess-liquidity-design.md`.

---

### Task 1: Carry ST/LT investments in `fetch_fundamentals`

**Files:**
- Modify: `gather_data.py` — `metrics` list (2774-2784), the us-gaap `_extra_tags` dict (~2860), `OVERRIDABLE_FUNDAMENTALS_FIELDS` (2623-2632).
- Test: `test_gather_data_investments.py` (new)

**Interfaces:**
- Produces: `fetch_fundamentals(...)` result carries per-year series `short_term_investments` and `long_term_investments` (list aligned to `years`, `None` where absent). Both added to `OVERRIDABLE_FUNDAMENTALS_FIELDS`.

- [ ] **Step 1: Write the failing test**

```python
# test_gather_data_investments.py
"""fetch_fundamentals must carry short/long-term investment series so the
ROCE excess-liquidity helper can read them."""

from unittest.mock import patch

import gather_data


def _facts():
    """Minimal SEC companyfacts payload: one us-gaap fact per tag, FY2023."""
    def unit(val):
        return {"units": {"USD": [
            {"end": "2023-12-31", "val": val, "fy": 2023, "fp": "FY", "form": "10-K"},
        ]}}
    return {"facts": {"us-gaap": {
        "Assets": unit(1_000_000_000),
        "LiabilitiesCurrent": unit(200_000_000),
        "OperatingIncomeLoss": unit(150_000_000),
        "ShortTermInvestments": unit(80_000_000),
        "LongTermInvestments": unit(120_000_000),
        "CashAndCashEquivalentsAtCarryingValue": unit(50_000_000),
    }}}


def test_fetch_fundamentals_carries_investment_series():
    with patch("gather_data.get_cik", return_value="0000000001"), \
         patch("gather_data.fetch_company_facts", return_value=_facts()):
        fund = gather_data.fetch_fundamentals("TEST", n_years=5)
    assert "short_term_investments" in fund
    assert "long_term_investments" in fund
    # values are in millions (EDGAR value / 1e6)
    assert fund["short_term_investments"][-1] == 80.0
    assert fund["long_term_investments"][-1] == 120.0


def test_investment_fields_are_overridable():
    assert "short_term_investments" in gather_data.OVERRIDABLE_FUNDAMENTALS_FIELDS
    assert "long_term_investments" in gather_data.OVERRIDABLE_FUNDAMENTALS_FIELDS
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest test_gather_data_investments.py -v`
Expected: FAIL — `short_term_investments` not in fund / not in OVERRIDABLE list.

- [ ] **Step 3: Implement**

In `gather_data.py`:

3a. Add to the `metrics` list (2774-2784), after the `pension_liabilities` line:
```python
        # Marketable holdings for the ROCE excess-liquidity strip
        "short_term_investments", "long_term_investments",
```

3b. Add to the us-gaap `_extra_tags` dict (the block starting ~2860 with `"total_assets": ["Assets"]`), two entries:
```python
            "short_term_investments": ["ShortTermInvestments",
                                       "MarketableSecuritiesCurrent",
                                       "AvailableForSaleSecuritiesCurrent",
                                       "AvailableForSaleSecuritiesDebtSecuritiesCurrent"],
            "long_term_investments": ["LongTermInvestments",
                                      "InvestmentsAndAdvances",
                                      "MarketableSecuritiesNoncurrent",
                                      "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent"],
```

3c. Add both keys to `OVERRIDABLE_FUNDAMENTALS_FIELDS` (2623-2632), after `pension_liabilities`:
```python
    "short_term_investments", "long_term_investments",
```

(The IFRS/20-F path needs no change: keys absent from `data_by_year` become `None` in the assembled series (gather_data.py:3091) and default to 0 in the ROCE helper.)

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest test_gather_data_investments.py -v`
Expected: PASS (2 tests). If the mocked `_facts()` shape doesn't match `_try_tags`/`_extract_annual_values` expectations, adjust the fixture to the real companyfacts shape (units→USD list of {end,val,fy,form}) until green — do not change production code to fit the test.

- [ ] **Step 5: Lint + commit**

Run: `python3 -m ruff check gather_data.py test_gather_data_investments.py`
```bash
git add gather_data.py test_gather_data_investments.py
git commit -m "feat(roce): carry short/long-term investment series in fetch_fundamentals"
```

---

### Task 2: `excess_liquidity` helper

**Files:**
- Modify: `scorecard_utils.py` (add after `FLOAT_CE_TA_THRESHOLD`, before `compute_roce_metric`)
- Test: `test_scorecard_utils.py`

**Interfaces:**
- Produces: `excess_liquidity(fund, i) -> float`. `liquid = (cash + short_term_investments + long_term_investments)[i]`; `debt = (total_debt + operating_lease_liabilities + finance_lease_liabilities)[i]`; returns `max(0, liquid − debt)`. Any missing series or `None` element counts as 0.

- [ ] **Step 1: Write the failing test** (append to `test_scorecard_utils.py`)

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest test_scorecard_utils.py -k excess_liquidity -v`
Expected: FAIL — `cannot import name 'excess_liquidity'`.

- [ ] **Step 3: Implement** (in `scorecard_utils.py`)

```python
ROCE_CEILING = 100.0  # per-year ROCE cap (%) for CE≤0 / capital-light names


def _at(fund, key, i):
    """Series element at year i, or 0.0 when the series or element is absent/None."""
    seq = fund.get(key) or []
    v = seq[i] if i < len(seq) else None
    return v if v is not None else 0.0


def excess_liquidity(fund, i):
    """Non-operating liquidity at year i: max(0, marketables − debt).

    marketables = cash + short_term_investments + long_term_investments
    debt        = total_debt + operating_lease_liabilities + finance_lease_liabilities
    Floored at 0 (net-debt names have no excess to strip).
    """
    liquid = (_at(fund, "cash", i)
              + _at(fund, "short_term_investments", i)
              + _at(fund, "long_term_investments", i))
    debt = (_at(fund, "total_debt", i)
            + _at(fund, "operating_lease_liabilities", i)
            + _at(fund, "finance_lease_liabilities", i))
    return max(0.0, liquid - debt)
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest test_scorecard_utils.py -k excess_liquidity -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Lint + commit**

```bash
python3 -m ruff check scorecard_utils.py test_scorecard_utils.py
git add scorecard_utils.py test_scorecard_utils.py
git commit -m "feat(roce): excess_liquidity helper (marketables − debt, floored)"
```

---

### Task 3: `capital_employed` + `roce_for_year` helpers (with ceiling cap)

**Files:**
- Modify: `scorecard_utils.py`
- Test: `test_scorecard_utils.py`

**Interfaces:**
- Consumes: `excess_liquidity`, `_at`, `ROCE_CEILING`.
- Produces:
  - `capital_employed(fund, i) -> float` = `total_assets[i] − current_liabilities[i] − excess_liquidity(fund, i)` (may be ≤ 0; not floored here).
  - `roce_for_year(fund, i) -> tuple[float | None, bool]` returning `(pct, capped)`:
    - Requires `operating_income[i]`, `total_assets[i]`, `current_liabilities[i]` present; otherwise `(None, False)`.
    - `ce = capital_employed(fund, i)`. If `ce <= 0` → `(ROCE_CEILING, True)`.
    - `pct = oi / ce * 100`. If `pct > ROCE_CEILING` → `(ROCE_CEILING, True)`, else `(pct, False)`.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest test_scorecard_utils.py -k "capital_employed or roce_for_year" -v`
Expected: FAIL — helpers not defined.

- [ ] **Step 3: Implement** (in `scorecard_utils.py`, after `excess_liquidity`)

```python
def capital_employed(fund, i):
    """TA − CL − excess_liquidity at year i. May be ≤ 0 for capital-light names
    (handled by roce_for_year's ceiling)."""
    return (_at(fund, "total_assets", i)
            - _at(fund, "current_liabilities", i)
            - excess_liquidity(fund, i))


def roce_for_year(fund, i):
    """Per-year ROCE = EBIT / (excess-liquidity-adjusted CE), with a ceiling cap.

    Returns (pct, capped). (None, False) when EBIT/TA/CL are unavailable.
    CE ≤ 0 → maximally capital-efficient → ceiling (a pass), year retained.
    """
    oi_seq = fund.get("operating_income") or []
    ta_seq = fund.get("total_assets") or []
    cl_seq = fund.get("current_liabilities") or []
    oi_v = oi_seq[i] if i < len(oi_seq) else None
    ta_v = ta_seq[i] if i < len(ta_seq) else None
    cl_v = cl_seq[i] if i < len(cl_seq) else None
    if oi_v is None or ta_v is None or cl_v is None:
        return (None, False)
    ce = capital_employed(fund, i)
    if ce <= 0:
        return (ROCE_CEILING, True)
    pct = oi_v / ce * 100
    if pct > ROCE_CEILING:
        return (ROCE_CEILING, True)
    return (pct, False)
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest test_scorecard_utils.py -k "capital_employed or roce_for_year" -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Lint + commit**

```bash
python3 -m ruff check scorecard_utils.py test_scorecard_utils.py
git add scorecard_utils.py test_scorecard_utils.py
git commit -m "feat(roce): capital_employed + roce_for_year helpers with ceiling cap"
```

---

### Task 4: Rewire `compute_roce_metric` to use the helpers

**Files:**
- Modify: `scorecard_utils.py` — `compute_roce_metric` (16-66)
- Test: `test_scorecard_utils.py`

**Interfaces:**
- Consumes: `roce_for_year`; keeps `(metric, avg_value)` return **unchanged** (all existing callers rely on the 2-tuple).
- Behaviour: `roce_pcts` now comes from `roce_for_year` (excess-adjusted, capped, CE≤0 years KEPT). `ce_ta_ratios` (the float test) STILL uses original `(ta − cl)/ta`. Metric selection and ROE path unchanged.

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest test_scorecard_utils.py -k "strips_excess or cash_rich_name or ce_negative_year" -v`
Expected: FAIL — old formula gives different values.

- [ ] **Step 3: Implement** — replace the ROCE/CE loop in `compute_roce_metric` (16-66)

Replace the existing loop that builds `roce_pcts, ce_ta_ratios` (lines ~39-48) with:

```python
    roce_pcts, ce_ta_ratios = [], []
    for i in range(n):
        ta_v = ta_w[i] if i < len(ta_w) else None
        cl_v = cl_w[i] if i < len(cl_w) else None
        # Float test uses the ORIGINAL CE = TA − CL (unchanged).
        if ta_v and ta_v > 0 and cl_v is not None:
            ce_orig = ta_v - cl_v
            ce_ta_ratios.append(max(ce_orig, 0) / ta_v)
        # ROCE value uses the excess-liquidity-adjusted CE, with ceiling cap.
        pct, _capped = roce_for_year(fund, i)
        if pct is not None:
            roce_pcts.append(pct)
```

Leave everything else (the `roe_pcts` loop, `avg_ce_ta`, override, metric selection, `avg_value`) exactly as is. Update the docstring line "cash are NOT subtracted" to note excess liquidity is now stripped from the ROCE value (float test still on TA−CL).

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest test_scorecard_utils.py -v`
Expected: PASS (all — new tests plus the pre-existing ROCE/ROE tests still green; the original `test_roce_uses_ta_minus_cl_no_goodwill_subtraction` uses a fund with no cash/investment/debt keys → excess 0 → still 25%).

- [ ] **Step 5: Lint + commit**

```bash
python3 -m ruff check scorecard_utils.py test_scorecard_utils.py
git add scorecard_utils.py test_scorecard_utils.py
git commit -m "feat(roce): compute_roce_metric strips excess liquidity; float-test unchanged"
```

---

### Task 5: Rewire the `mcp_server.py` per-year ROCE loop + capped flag

**Files:**
- Modify: `mcp_server.py` — per-year `roce` loop (674-687) and `_compute_fundamentals_headline` (690+, add `roce_capped`)
- Test: `test_mcp_server.py`

**Interfaces:**
- Consumes: `scorecard_utils.roce_for_year`.
- Behaviour: `roce_latest_pct` / `roce_rising` computed from `roce_for_year(fund, i)` (same helper as the mean, so they cannot diverge). `_compute_fundamentals_headline` sets `headline["roce_capped"] = True` when any contributing year was capped.

- [ ] **Step 1: Write the failing test** (append to `test_mcp_server.py`; import `roce_for_year` path is internal — test via the headline)

```python
def test_headline_roce_uses_excess_adjusted_and_flags_capped():
    import mcp_server
    fund = {
        "years": [2022, 2023],
        "operating_income": [30.0, 20.0],
        "total_assets": [50.0, 100.0],
        "current_liabilities": [10.0, 20.0],
        "cash": [200.0, 0.0],
        "short_term_investments": [0.0, 0.0], "long_term_investments": [0.0, 0.0],
        "total_debt": [0.0, 100.0],
        "operating_lease_liabilities": [0.0, 0.0], "finance_lease_liabilities": [0.0, 0.0],
        "fcf": [None, None], "cash_flow": [None, None],
    }
    h = mcp_server._compute_fundamentals_headline(fund, {})
    # latest year (2023) is net-debt normal ROCE 20/80 = 25%
    assert round(h["roce_latest_pct"], 1) == 25.0
    # yr0 was capped (CE≤0) → headline flags it
    assert h["roce_capped"] is True
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest test_mcp_server.py -k headline_roce_uses_excess -v`
Expected: FAIL — `roce_latest_pct` uses old TA−CL (would be 20/80=25% here by coincidence for 2023, but `roce_capped` key is missing → KeyError/assert fails).

- [ ] **Step 3: Implement** — replace the per-year `roce` loop (mcp_server.py:674-686)

```python
    # Latest-year ROCE + rising trend, excess-liquidity-adjusted (shared helper
    # with compute_roce_metric so mean and latest/trend cannot diverge).
    from scorecard_utils import roce_for_year
    roce = {}
    any_capped = False
    for i in range(n):
        pct, capped = roce_for_year(fund, i)
        if pct is not None:
            roce[i] = pct
            any_capped = any_capped or capped
    rk = sorted(roce)
    if rk:
        out["roce_latest_pct"] = roce[rk[-1]]
        if len(rk) >= 2:
            out["roce_rising"] = roce[rk[-1]] > roce[rk[max(0, len(rk) - 4)]]
    out["roce_capped"] = any_capped
    return out
```

(If `_compute_fundamentals_headline` builds its `headline` dict separately from this `out`, also surface `roce_capped` there: after `headline["avg_roce_pct"] = ...` at line ~715, add `headline["roce_capped"] = <the out dict>.get("roce_capped", False)` using whatever variable holds the per-year `out`. Inspect lines 660-716 to wire it to the actual variable names; keep the flag on the object the robustness/detail layer reads.)

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest test_mcp_server.py -k headline_roce -v`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
python3 -m ruff check mcp_server.py test_mcp_server.py
git add mcp_server.py test_mcp_server.py
git commit -m "feat(roce): mcp per-year ROCE uses shared excess-adjusted helper + capped flag"
```

---

### Task 6: Regression sweep

**Files:**
- Test only: run existing suites; inspect `test_robustness.py` / `test_mcp_server.py` for ROCE-band / `verdict_mapped` assumptions.

- [ ] **Step 1: Full suite**

Run: `python3 -m pytest test_scorecard_utils.py test_mcp_server.py tests/test_robustness.py test_tastytrade_api.py test_ibkr_api.py test_gather_data_investments.py -v`
Expected: all pass. If a robustness/mcp test hard-codes a ROCE band or `verdict_mapped` computed under the old (cash-in) CE, update the expected value to the excess-adjusted number and note why in the test.

- [ ] **Step 2: Lint whole repo**

Run: `python3 -m ruff check .`
Expected: clean.

- [ ] **Step 3: Commit any test updates**

```bash
git add -A
git commit -m "test(roce): update robustness/mcp expectations for excess-adjusted ROCE"
```

---

## Self-Review

**Spec coverage:** §1 motivation → all tasks; §2 definition → Global Constraints + Tasks 2-3; §3 CE≤0 cap → Task 3 (`ROCE_CEILING`, CE≤0 kept at ceiling) + capped flag Task 5; §4 one debt source → helpers read the same `fund` debt/lease fields the net-debt headline uses; §5 shared helper → Tasks 2-3 used by Tasks 4-5; §6 float-test unchanged → Task 4 (ce_ta_ratios on TA−CL) + explicit test; §7 scope → ROCE branch only, no toggle; §8 blast radius → Tasks 1 (gather_data), 4 (scorecard_utils), 5 (mcp_server); §9 tests → Tasks 2-5 each add the named cases.

**Placeholder scan:** No TBD/TODO. Every code step has complete code. The only "inspect and wire" note is Task 5 Step 3's `roce_capped` surfacing, which is explicit about what to check and why (the exact variable name depends on unread lines 660-716).

**Type consistency:** `roce_for_year` returns `(float|None, bool)` — consumed consistently in Task 4 (uses `pct`) and Task 5 (uses `pct, capped`). `excess_liquidity`/`capital_employed` return `float`. `compute_roce_metric` keeps `(metric, avg_value)`. `_at(fund, key, i)` helper shared across Tasks 2-3. `ROCE_CEILING = 100.0` defined once (Task 2), used in Task 3.
