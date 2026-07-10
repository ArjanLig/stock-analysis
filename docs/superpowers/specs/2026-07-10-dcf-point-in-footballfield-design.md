# DCF as a single point in the watchlist football field — design

**Date:** 2026-07-10
**Status:** Approved (brainstorming)

## Problem

With `discount_mode = opportunity_cost` (ke = rf + ERP = expected index return), the
DCF intrinsic value is the price at which the buyer's expected return equals the
index — a single, actionable "index break-even" number. In the watchlist details
tooltip (`_render_football_field`) the DCF currently renders as a range bar
(fv_low–fv_high scenario spread), which obscures that single number.

## Decision

The DCF row shows a **single point** (its `fv_mid`) instead of a range bar. Every
other lens (Peers, Historical, Dividend, SOTP) keeps its range bar unchanged. The
watchlist headline cell (`_render_fv_cell`, the blend mid + range bar) is untouched.

## Change

In `streamlit_app._render_football_field`, special-case the `dcf` lens row:
- Keep the `.ff-bar` track (preserves shared-axis alignment; `count('ff-bar') == 5`
  stays true).
- Inside it, render a single `.ff-point` dot positioned at `_x(dcf_fv_mid)` in the
  accent colour, instead of the `.ff-range` fill.
- Range-label shows `${mid} · index break-even` instead of `${low} — ${high}`.
- Add a `.ff-point` CSS rule.
- If the DCF lens is absent (None) or its `fv_mid` is None, fall back to the existing
  behaviour (grey "(skipped)" row, or a normal bar) — no crash.

All other lens rows, the price marker, and the container are unchanged.

## Scope

Watchlist details tooltip only. The full detail page is out of scope. Streamlit-only,
pure HTML-string builder, offline-testable.

## Tests

- Update `test_render_football_field_renders_all_active_lenses`: DCF now renders one
  `.ff-point` and the `index break-even` label; the other four lenses still render
  `.ff-range` bars; `count('ff-bar') == 5` holds.
- New test: DCF is a single point (exactly one `.ff-point`), labeled with its mid and
  `index break-even`, and does NOT emit a `$low — $high` range for the DCF row.
- `test_render_football_field_handles_missing_lens` / `_handles_no_summary` unchanged
  (DCF-None still greys out; empty summary still returns a placeholder).
