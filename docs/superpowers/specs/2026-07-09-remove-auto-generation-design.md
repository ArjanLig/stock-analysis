# Remove all DCF auto-generation — design

**Date:** 2026-07-09
**Status:** Approved (brainstorming)

## Problem

Configs are authored manually through the MCP (Claude Desktop). The pipeline's
automatic generation of forward-looking assumptions produces poor output:
auto-selected peers are weak, derived DCF growth/margin curves are unreliable,
and the valuation-time yfinance autofills mutate the config behind the user's
back — which mainly *confuses* the MCP workflow. On the deployed Cloud Run MCP,
yfinance is 429-blocked anyway, so those autofills already fail silently.

The user wants all auto-generated **guesswork** gone, while keeping the
auto-fetched **facts** (which they rely on — e.g. the EDGAR fundamentals fixed
earlier this session).

## The fact / guesswork boundary

**Kept — facts (auto-fetched, not guessed):**
- `base_revenue`, `base_oi`, `base_op_margin` (last actual year)
- `shares_outstanding`, `stock_price`, `market_cap`, `equity_market_value`, `debt_market_value`
- `risk_free_rate` (Treasury), `tax_rate` (from 10-K)
- the full historical `fundamentals` arrays
- `credit_spread` (deterministic from real interest coverage → Damodaran table; treated as fact-like, like `tax_rate`)
- `sector_betas` (SIC → sector name; the β value is inert under `discount_mode = opportunity_cost`, only the sector *name* is still read, for the margin lookup)

**Removed — guesswork:**
- `revenue_growth` — 10y exponential-decay curve from CAGRs + consensus + size caps
- `op_margins` — projected margin curve
- peer auto-selection (`find_peers` + `fetch_peer_data`)
- the 3 valuation-time autofills (`auto_fetch.py`): forward EPS, ttm EBITDA,
  historical multiples, peer fwd_pe/ev_ebitda, dividend fields

## Design decisions

1. **Placeholders, not empty:** removed projections are replaced by neutral,
   directly-computable placeholders so a fresh config never KeyErrors and yields
   a valid (if "dumb") valuation until the user refines it via MCP.
2. **Hard removal, no flag:** the generation code is deleted, not gated. The CLI
   and Streamlit paths therefore also emit bare configs. (Reversible via git if
   ever needed.)

## Changes

### Block 1 — Projections → neutral placeholders (`gather_data.build_config`)

Delete the growth derivation (`gather_data.py:~2414-2478`) and margin derivation
(`~2540-2559`). Replace with:

```python
revenue_growth = [term_growth] * 10        # flat at terminal growth
op_margins     = [base_op_margin] * 10      # flat at last actual margin
```

`base_revenue`, `base_oi`, `base_op_margin`, `term_growth` (default 2.5%),
`tax_rate`, `sales_to_capital` stay. The `consensus` parameter becomes dead;
remove it from `build_config` (and the `_build_dcf_config_impl` /
`run_analysis` / CLI call sites) if clean to do so, otherwise leave it ignored.
`real`-basis handling (`nominal_revenue_growth`) still derives from the flat
`revenue_growth`, so it keeps working unchanged.

### Block 2 — Remove peer auto-selection

Only the **auto-selection** (`find_peers`, SIC → peer tickers) is guesswork.
`fetch_peer_data` (fetch market data for a *given* list of tickers) is **kept** —
it is dual-use: the Streamlit "add peer manually" flows call it for a peer the
user explicitly chose (`streamlit_app.py:5653`, `:8256`). That is a fact-fetch,
not guesswork.

Stop calling `find_peers` (and the `fetch_peer_data` call that consumes its
output) in the config-build path; set `peers = []`:
- `mcp_server.py:167-177`
- Streamlit `run_analysis` peer block (`streamlit_app.py:8506-8521`)
- `gather_data.py` CLI main (`--auto-peers` / `--peers auto`, `~3678-3687`)

`find_peers` then has no remaining callers → remove the function. `fetch_peer_data`
**stays** (manual peer-add depends on it). Also delete `scripts/backfill_peers.py`
(the watchlist-wide auto-peer script — the peer analogue of `force_refresh_all.py`).

### Block 3 — Remove the valuation autofills

Delete `auto_fetch.py` and every call site:
- `mcp_server.py:248-250` and `312-314`
  (so `calculate_multi_lens_valuation` and `refresh_all_valuations` no longer
  touch the config with yfinance data)
- `streamlit_app.py:707-709` and the re-export block at `~658-663`
- `scripts/force_refresh_all.py` — exists only to drive these; delete it
  (and update the stale comment referencing it in `valuation_lenses.py:36`)
- tests: remove the autofill tests in `tests/test_market_data.py` (~10) and the
  autofill monkeypatches in `tests/test_mcp_server_user_id.py`

After removal, refresh/calculate paths compute the summary from **only** what is
already in the config.

## Out of scope (explicitly unchanged)

- Margin-of-safety logic
- `discount_mode` / WACC engine (separate, already changed this session)
- EDGAR fact fetching
- The DCF math in `compute_intrinsic_value`

## Testing

- New test: `build_config` output has `revenue_growth == [term_growth]*10`,
  `op_margins == [base_op_margin]*10`, and `peers == []`.
- New/updated test: `calculate_multi_lens_valuation` and `refresh_all_valuations`
  do not call out to yfinance (no autofill) — the config is unchanged except for
  `valuation_summary`.
- Remove obsolete autofill tests.
- Full suite (`tests/` + `test_tastytrade_api.py` + `test_ibkr_api.py`) and
  `ruff check .` green. (Pre-existing failure
  `test_market_data.py::test_fetch_dividend_history_full_5y_payer` is unrelated
  and already red on `main`.)

## Blast radius summary

| File | Change |
|------|--------|
| `gather_data.py` | flat placeholders; drop growth/margin derivation; drop `find_peers` + its CLI call; drop `consensus` derivation |
| `mcp_server.py` | drop `find_peers` lookup (`peers=[]`) + 3 autofill calls (×2 sites) |
| `streamlit_app.py` | `peers=[]` in `run_analysis` (drop `find_peers`); drop autofill calls + re-export. **Keep** `fetch_peer_data` import + manual peer-add flows |
| `auto_fetch.py` | delete |
| `scripts/force_refresh_all.py` | delete |
| `scripts/backfill_peers.py` | delete (watchlist-wide auto-peer script) |
| `valuation_lenses.py` | update stale comment referencing force_refresh_all |
| `test_mcp_server.py` (root) | update peer-selection tests (`build_dcf_config` now yields `peers=[]`) |
| `tests/test_market_data.py` | remove autofill tests |
| `tests/test_mcp_server_user_id.py` | remove autofill monkeypatches |
| `tests/` (new) | placeholder + no-autofill assertions |
