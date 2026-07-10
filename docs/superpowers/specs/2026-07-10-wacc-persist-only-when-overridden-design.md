# Persist per-year / terminal WACC only when overridden — design

**Date:** 2026-07-10
**Status:** Approved (brainstorming)

## Problem

The DCF editor always writes `cfg['wacc_per_year']` and `cfg['terminal_wacc']` back
to the config on every render (`streamlit_app.py:5356, 5363`). Those stored values
are read first on the next render (`:5122, :5138`) and by `compute_intrinsic_value`
(`dcf_calculator`), so an auto-computed WACC gets frozen into the config. When the
discount mode changed to opportunity_cost, every config carried a stale CAPM WACC,
making the detail page disagree with the (freshly-recomputed) watchlist multi-lens.
A one-off Supabase strip fixed the current data, but the editor would re-freeze on
the next visit and the drift recurs on any future rf/ERP change.

## Decision

Persist `wacc_per_year` / `terminal_wacc` **only when the user overrides** the live
`compute_wacc` default. When the per-year values equal the live default (the common
case — nobody hand-tunes WACC), remove the keys so the discount rate is always taken
live. "Equal" is compared at display precision (2 decimals of a percent), which is
exactly the granularity the editor's number inputs expose — this sidesteps
float-rounding false positives.

## Change

New pure module-level helper in `streamlit_app.py`:

```python
def _apply_wacc_persistence(cfg, wacc_list, tv_wacc, default_wacc):
    """Persist per-year / terminal WACC only when they deviate from the live
    compute_wacc default (compared at 2-decimal-percent display precision).
    Otherwise remove them so the discount rate is taken live — prevents
    frozen-WACC drift after an rf/ERP change. Mutates cfg; returns
    (wacc_overridden: bool, tv_overridden: bool)."""
    default_pct = round(default_wacc * 100, 2)
    wacc_overridden = any(round(w * 100, 2) != default_pct for w in wacc_list)
    tv_overridden = round(tv_wacc * 100, 2) != default_pct
    if wacc_overridden:
        cfg['wacc_per_year'] = wacc_list
    else:
        cfg.pop('wacc_per_year', None)
    if tv_overridden:
        cfg['terminal_wacc'] = tv_wacc
    else:
        cfg.pop('terminal_wacc', None)
    return wacc_overridden, tv_overridden
```

In the editor write-back (`~5354-5368`): drop the unconditional
`cfg['wacc_per_year'] = _wacc_list` and `cfg['terminal_wacc'] = _tv_wacc`; call
`_apply_wacc_persistence(cfg, _wacc_list, _tv_wacc, _default_wacc)` instead, and build
`_new_snapshot` from the persisted state (`tuple(cfg.get('wacc_per_year', []))`,
`cfg.get('terminal_wacc')`) so change-detection stays correct and no save-loop occurs.

## Scope

`streamlit_app.py` only (the editor). `compute_intrinsic_value` already falls back to
`compute_wacc` when `wacc_per_year` is absent — no change there. Streamlit-only deploy
(not in the Cloud Run MCP image). The per-year WACC override feature still works: a
genuine edit deviates from the default and is persisted.

## Tests

- All per-year == default (exact) → `wacc_per_year` removed; returns `(False, ...)`.
- All per-year == default but at rounded display value (e.g. 0.0893 vs default
  0.089269) → still removed (round-equal).
- One per-year edited away from default → `wacc_per_year` persisted with the list;
  returns `(True, ...)`.
- `terminal_wacc` == default → removed; edited → persisted.
- Pre-existing stale `wacc_per_year` equal to the live default → removed (self-heal on
  first visit).
