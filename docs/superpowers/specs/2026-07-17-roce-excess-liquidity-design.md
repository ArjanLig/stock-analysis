# Excess-liquidity correction in the ROCE gate — design

Date: 2026-07-17
Status: approved (user-authored spec), ready for implementation plan

## 1. Motivation

Prasad-faithful ROCE: remove non-operating liquidity from capital so
capital-light compounders are not unfairly held to a cash-/goodwill-heavy
denominator. We strip **excess liquidity** (not "excess cash") because ALL
marketable holdings count — cash plus short-term AND long-term investments —
not only cash & equivalents.

## 2. Definition (per year i)

```
liquid[i]           = cash[i] + short_term_investments[i] + long_term_investments[i]
debt[i]             = total_debt[i] + operating_lease_liab[i] + finance_lease_liab[i]
excess_liquidity[i] = max(0, liquid[i] − debt[i])
CE[i]               = total_assets[i] − current_liabilities[i] − excess_liquidity[i]
ROCE                = mean( EBIT[i] / CE[i] )   over valid years
```

Net debt (debt ≥ liquid) → excess_liquidity = 0 → CE = TA − CL. Floor at zero:
you remove liquidity, you never ADD cash to capital.

Missing components default to 0 (e.g. a filer that reports no long-term
investments contributes 0 to `liquid`).

## 3. Edge case: CE small or ≤ 0

For very capital-light names (VEEV) `excess_liquidity` can exceed `TA − CL`,
making CE ≤ 0 and ROCE explode. Handling:

- Cap per-year ROCE at a ceiling: **100%**.
- CE ≤ 0 → treat as maximally capital-efficient → per-year ROCE = ceiling
  (counts as a pass); **do not drop the year**. Dropping would remove the
  most capital-light year from the mean and penalise the best names — wrong
  for a quality gate.
- The cap is flagged visibly in the output (so a capped year is distinguishable
  from a genuine 100% ROCE).

## 4. Leases / debt — one source

The debt side (`total_debt + operating + finance leases`) draws from the same
source as `debt_breakdown` / net-debt elsewhere in the config (the per-year
`total_debt`, `operating_lease_liabilities`, `finance_lease_liabilities` fields
on `fund`, as used by the net-debt headline at `mcp_server.py:732-733`). Do not
redefine "debt" separately — one place where "debt" lives, so the lease
treatment here and in the net-debt band never diverge.

## 5. Shared helper (DRY)

New in `scorecard_utils.py`:

```
excess_liquidity(fund, i) -> float
capital_employed(fund, i) -> float   # = TA − CL − excess_liquidity, incl. floor logic
```

Both call-sites use these helpers:
- `compute_roce_metric` (the mean)
- `mcp_server.py`, per-year `roce` loop (lines 674–686; latest / rising trend)

So the mean and the latest/trend value cannot diverge by construction. The
per-year ROCE cap (§3) lives with these helpers too (a single
`roce_for_year(fund, i) -> (pct, capped: bool)` is the natural shape), so both
call-sites apply the cap identically.

## 6. Float detection UNCHANGED — critical

- The ROE fallback for float businesses (banks/settlement) tests on the
  ORIGINAL CE = TA − CL. Threshold `(TA − CL)/TA < 0.25` stays exactly as is.
- Two separate denominators, deliberately: float-test = `TA − CL`; ROCE value =
  `TA − CL − excess_liquidity`.
- Reason: if the float-test also subtracted excess liquidity, cash-rich names
  (Veeva) would wrongly flip to the ROE branch.

## 7. Scope

- ROCE branch only. The ROE fallback (denominator = equity) is unchanged.
- Portfolio-wide as a methodology change, no per-config toggle — consistent with
  the rollout of SBC-removal and the opportunity-cost discount rate.

## 8. Blast radius

- **`scorecard_utils.py`**: new helpers (`excess_liquidity`, `capital_employed`,
  per-year `roce_for_year` with cap) + `compute_roce_metric` uses them.
- **`mcp_server.py`**: per-year `roce` loop (lines 674–686) uses the shared
  helper (drops its own inline `oi_v / (ta_v - cl_v)`).
- **`gather_data.py` `fetch_fundamentals`** (added dependency — NOT in the
  user's original §8, but required by §2): extract `short_term_investments`
  (EDGAR tag `ShortTermInvestments`, …) and `long_term_investments` (EDGAR tag
  `LongTermInvestments`, …) into the per-year `fund` dict. Today `fund` carries
  `cash`, `total_debt`, `operating_lease_liabilities`,
  `finance_lease_liabilities` but NOT the two investment series (verified:
  they exist only in the DCF-config path, not in `fetch_fundamentals`). Add both
  to `OVERRIDABLE_FUNDAMENTALS_FIELDS` so per-year overrides work, and default
  missing years to 0. The IFRS/20-F fundamentals path (`_parse_financials_ifrs`)
  must carry them too, or default 0, so foreign filers don't crash.

## 9. Tests

Extend `test_scorecard_utils.py`:
- net-cash vs net-debt (strip vs no strip);
- floor-at-zero (debt > liquid → excess = 0);
- LT investments counted in `liquid` (regression: a PANW/VEEV-like case where
  long-term investments are the bulk of the surplus);
- CE ≤ 0 → cap/pass behaviour per §3 (per-year ROCE = ceiling, year kept, capped
  flag set);
- float-test unchanged: an explicit test that a cash-rich name does NOT flip to
  ROE after the excess-liquidity strip.

Plus: check `robustness`/`mcp` tests for a consistent `roce` band and
`verdict_mapped` (the net-debt band and the ROCE band must both still compute).

## Open decision carried from the user's spec

§3 (CE ≤ 0 handling) was flagged "te bevestigen keuze". This spec adopts the
user's proposal verbatim: per-year ROCE cap = 100%, CE ≤ 0 → pass at the
ceiling, year retained, cap flagged. The ceiling value (100%) is the one knob to
confirm at review; everything else in §3 is settled.
