# Cashflow Champions — rank within sector

**Date:** 2026-08-04
**Status:** approved, ready for implementation

## Problem

The screen's top ranks are dominated by commodity producers at the top of their
cycle — 5 of the top 10 in the 2026-06-26 snapshot were oil & gas or fertiliser
(APA #1, DVN #2, CF #6, EOG #9, EXE #10). The owner does not want to buy into
those at a cycle peak.

The mechanism is structural: a single year's CFO feeds *both* axes. Cash ROA is
`CFO / total_assets` and the value axis is `CFO / market_cap`, so a peak year
lifts quality and cheapness simultaneously. A low P/CF on peak cash flow reads
as a bargain.

## What was ruled out, and why

Five statistical approaches were measured against the full 514-name universe
before settling on sector-relative ranking. All are recorded here so they are
not re-attempted.

| Approach | Result |
|---|---|
| Median CFO/assets over history | Energy unchanged in top 20 (5 → 5); BIIB 260 → 7 and ALGN 295 → 16, i.e. names in secular decline flattered by their past |
| `min(median, latest)` | Energy got *worse* (5 → 6); APA back to #1 |
| Volatility (CV of CFO/assets) filter | Does not separate: V 0.46 and MO 0.37 are noisier than EOG 0.29 and KLAC 0.25 |
| Free cash flow instead of CFO | Capex tags unusable for exactly the names that matter (APA and COP have none, EOG reads 3% where reality is ~50%), and MSFT 63% / META 60% now rank as capital-hungry too |
| Peak indicator (latest CFO vs own median) | Inverted: APA 1.37× and DVN 1.23× are the *least* peaky, while MELI 52×, ADBE 4.6×, META 4.3× are flagged, because growers were small in the past |

**Conclusion:** a company's own cash-flow history carries no signal that
identifies "commodity producer at a cycle peak". What separates APA from ACN is
that its selling price is set by a global market it does not control — a
property of the sector, not of the numbers. Shale producers also wrote down
their asset base heavily in 2015-2020, so CFO/assets stays structurally high
even mid-cycle and there is nothing to average away.

## Design

Rank within sector instead of globally. The owner then browses per sector and
can hide the ones they are not interested in today.

### Sector source

`GICS Sector` from the S&P 500 CSV that `refresh_universe` already downloads —
the column is currently parsed and discarded. No new dependency, no extra
request. Eleven recognisable sectors.

Coverage on the current universe: 389 of 396 rankable names. The 7 gaps are
Nasdaq-100 members outside the S&P 500 and are filled by an explicit
`GICS_OVERRIDES` table (MELI, PDD, SHOP, ARM, ZS, ALNY, CAG). A future name
with neither source lands in sector `None`, is never flagged, and shows the
reason — it fails visibly rather than silently.

`refresh_universe` stores `gics_sector` and `gics_sub_industry` per constituent.

### Ranking

Formulas are unchanged: `cash_roa = CFO / total_assets`, value axis
`CFO / market_cap`, composite = mean of the two percentile ranks. The only
change is that percentiles are computed **within each sector group** rather
than across the whole universe.

- `is_champion` = top `top_pct` (0.20) of the name's own sector
- A sector with fewer than `MIN_SECTOR_SIZE` (5) names produces no champions;
  its rows carry a reason so the page can explain the gap. This prevents the
  degenerate case where Real Estate's single name (CSGP) crowns itself.
- Names with no known sector are ranked nowhere and never flagged.
- The global rank is retained alongside the sector rank so the full-universe
  table keeps one continuous sort order.

`ChampRow` gains `sector`, `sector_rank`, `sector_size`.

Expected outcome: ~83 champions (vs 80), with Energy contributing 4 out of its
own group of 18 instead of 5 of the global top 10.

### Known wrinkle

GICS Financials is not the same set as the SIC-based financial exclusion, so
FDS, FIS and FISV survive the SIC filter and form a GICS-Financials group of
12. This is a GICS quirk (data and payment businesses classify as Financials).
The SIC exclusion is left alone — it does what it should — and the divergent
label is accepted.

### Page

- A sector `st.multiselect` above the Champions table, all selected by default.
  Unchecking Energy and Materials removes APA, DVN, EXE, CF and NEM in one
  click. This is the owner's original request, made reversible.
- One Champions table, not eleven: a `Sector` column is added, `#` becomes the
  rank within sector, sorted by sector then rank. Eleven stacked tables would
  look tidier but cost endless scrolling; a filter achieves the same in place.
- The full-universe table gains the same `Sector` column.
- A snapshot predating this change (no sector fields) renders with a notice to
  re-run the computation, rather than an empty or misleading table.

### Rollout order

Mandatory: `--refresh-universe` first (otherwise no sectors exist), then
`--compute --store`.

### Tests

Offline, fixture-driven, in `test_cashflow_champions.py`:

- GICS columns from the CSV reach the universe file
- `GICS_OVERRIDES` fills the known gaps
- `rank_universe` percentiles are computed per sector, not globally
- champion flag is the top 20% of each sector
- a sector under `MIN_SECTOR_SIZE` yields zero champions, with a reason
- a name with an unknown sector is never flagged and carries a reason
- a snapshot without sector fields does not break the page helper

### Out of scope

The ranking formulas, the financials exclusion, and any cycle-normalisation of
cash flow. The experiments above are discarded, not parked.
