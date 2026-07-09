# Remove DCF Auto-Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Strip all auto-generated DCF guesswork (projection curves, peer auto-selection, valuation-time yfinance autofills) so configs are authored purely via the MCP, while keeping auto-fetched facts.

**Architecture:** Three independent removals. (1) `build_config` emits flat neutral placeholders instead of derived growth/margin curves. (2) Peer auto-selection (`find_peers`) is removed from all three config-build paths; `fetch_peer_data` stays for manual peer-add. (3) The `auto_fetch.py` layer and its call sites in the valuation/refresh paths are deleted. Facts (EDGAR fundamentals, price, shares, Treasury rf, tax rate, credit_spread, sector name) are untouched.

**Tech Stack:** Python 3.13, pytest, ruff. Offline unit tests (no network).

## Global Constraints

- `python3 -m ruff check .` must pass (config in `ruff.toml`).
- `python3 -m pytest test_tastytrade_api.py test_ibkr_api.py -v` — 81 tests, all must pass.
- Pre-existing unrelated failure `tests/test_market_data.py::test_fetch_dividend_history_full_5y_payer` is red on `main` — ignore it, do not "fix" it here.
- No network in unit tests. `build_config` is pure (takes `financials` as input); reuse `test_mcp_server._make_test_financials()`.
- Keep as facts (do NOT remove): `credit_spread`, `sector_betas` (SIC→name), all EDGAR fact fetching, `fetch_peer_data`, and the fetch primitives `fetch_historical_multiples` / `fetch_market_inputs` / `fetch_historical_forward_pe` / `fetch_dividend_history` (only the autofill *layer* over them is removed).
- Margin-of-safety logic and the `discount_mode`/WACC engine are out of scope — do not touch.

---

### Task 1: build_config emits neutral placeholders

**Files:**
- Modify: `gather_data.py:2414-2483` (revenue growth block), `gather_data.py:2485-2559` (op margin block)
- Test: `test_mcp_server.py` (append near the existing `test_build_config_*` tests, ~line 384)

**Interfaces:**
- Consumes: nothing new.
- Produces: `build_config(...)` returns a cfg where `cfg["revenue_growth"] == [term_growth]*10` (nominal) and `cfg["op_margins"] == [base_op_margin]*10`, `cfg["terminal_margin"] == base_op_margin`. `terminal_growth` default stays `TERMINAL_GROWTH_DEFAULT` (0.025 nominal). Signature unchanged (`sector_margin`, `consensus` become ignored params).

- [ ] **Step 1: Write the failing test**

Append to `test_mcp_server.py`:

```python
def test_build_config_uses_neutral_placeholders():
    """Auto-derived projection curves are removed: revenue_growth is flat at
    terminal growth, op_margins flat at the last actual margin, and no peers
    are auto-selected. Assumptions are authored via the MCP, not guessed."""
    import gather_data
    financials = _make_test_financials()
    cfg = gather_data.build_config(
        ticker="TEST", financials=financials, stock_price=100.0,
        market_cap=100000, shares_yahoo=1000, risk_free_rate=0.04,
        sector_betas=[("Tech", 1.0, 1.0)], credit_spread=0.01,
        credit_rating="A", peers=[], company_name="Test Corp",
        sector_margin=0.30,          # must be IGNORED now
        consensus={"growth_current_year": 0.40},  # must be IGNORED now
    )
    # base_op_margin = oi[-1]/rev[-1] = 23000/95000 = 0.242
    assert cfg["revenue_growth"] == [0.025] * 10   # flat at nominal terminal growth
    assert cfg["op_margins"] == [0.242] * 10       # flat at last actual margin
    assert cfg["terminal_margin"] == 0.242
    assert cfg["peers"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test_mcp_server.py::test_build_config_uses_neutral_placeholders -v`
Expected: FAIL — current code derives a decayed curve, so `revenue_growth` is not flat.

- [ ] **Step 3: Replace the growth derivation block**

In `gather_data.py`, replace the whole revenue-growth block. Delete from the line `# ── [IMPROVEMENT 1 & 3 & 4 & 5] Revenue growth assumptions ──` (currently line 2414) through the real-basis deflation that ends at line 2483, and replace with:

```python
    # ── Revenue growth: neutral placeholder ──
    # Auto-derived projection curves were removed — forward assumptions are
    # authored via the MCP, not guessed here. Flat at terminal growth.
    revenue_growth = [term_growth] * 10

    # Deflate revenue growth for real valuation
    if valuation_basis == "real" and breakeven_inflation is not None:
        nominal_revenue_growth = list(revenue_growth)
        revenue_growth = [max(g - breakeven_inflation, 0.0) for g in revenue_growth]
```

- [ ] **Step 4: Replace the operating-margin derivation block**

In `gather_data.py`, replace the whole op-margin block. Delete from the line `# ── [IMPROVEMENT 1 & 2] Operating margin trajectory ──` (currently line 2485) through the end of the margin loop and its prints at line 2559, and replace with:

```python
    # ── Operating margin: neutral placeholder ──
    # Flat at the last actual margin; terminal margin = same. No sector-blend
    # or trend-extrapolation guesswork.
    op_margins = [base_op_margin] * 10
    term_margin = base_op_margin
```

Note: `sector_margin` and `consensus` parameters are now unused inside the function — leave them in the signature (callers still pass them) but they are ignored. `trend`, `margin_slope`, `cagr_*`, `decay_lambda`, `hist_margins` are fully removed with the blocks above and are not referenced downstream (verified: only `term_margin`, `revenue_growth`, `op_margins`, `nominal_revenue_growth` are used later).

- [ ] **Step 5: Run the test to verify it passes**

Run: `python3 -m pytest test_mcp_server.py::test_build_config_uses_neutral_placeholders test_mcp_server.py::test_build_config_nominal_default test_mcp_server.py::test_build_config_real_mode -v`
Expected: PASS (all three — the real-mode test still deflates the now-flat curve).

- [ ] **Step 6: Lint**

Run: `python3 -m ruff check gather_data.py test_mcp_server.py`
Expected: `All checks passed!` (if ruff flags `sector_margin`/`consensus`/`sector_betas` as unused *locals*, that is fine — they are parameters, not locals; if it flags a genuinely now-unused *local* like `recent_margin`, delete that leftover line).

- [ ] **Step 7: Commit**

```bash
git add gather_data.py test_mcp_server.py
git commit -m "build_config: flat neutral placeholders, drop derived growth/margin curves

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Remove peer auto-selection

**Files:**
- Modify: `mcp_server.py:167-177`, `gather_data.py:3674-3687` (CLI peer block) + `gather_data.py:2137-2239` (`find_peers` def) + argparse `--auto-peers`/`--n-peers`, `streamlit_app.py:40` (import) + `streamlit_app.py:8506-8521` (run_analysis peer block)
- Delete: `scripts/backfill_peers.py`
- Test: `test_mcp_server.py:97` (`test_build_dcf_config_tool`)

**Interfaces:**
- Consumes: nothing new.
- Produces: `_build_dcf_config_impl` calls `build_config` with `peers=[]`. `find_peers` no longer exists. `fetch_peer_data` unchanged (kept for manual peer-add).

- [ ] **Step 1: Update the failing test first**

In `test_mcp_server.py`, edit `test_build_dcf_config_tool` (line ~97). Remove the `find_peers`/`fetch_peer_data` mock setup and the final assertion, and assert peers is empty. Replace the two mock lines:

```python
        mock_gd.find_peers.return_value = ["AAPL", "GOOGL"]
        mock_gd.fetch_peer_data.return_value = [
            {"ticker": "AAPL", "name": "Apple", "ev_revenue": 9.5, "ev_ebitda": 26.0,
             "pe": 33.5, "op_margin": 0.315, "rev_growth": 0.05, "roic": 0.55},
        ]
```

with:

```python
        # Peer auto-selection removed — build_dcf_config no longer selects peers.
```

and replace the final assertion:

```python
        # Verify fetch_peer_data was called (not just find_peers)
        mock_gd.fetch_peer_data.assert_called_once_with(["AAPL", "GOOGL"])
```

with:

```python
        # No peer auto-selection: build_config receives an empty peers list.
        assert call_kwargs.kwargs.get("peers") == []
```

Also update the docstring line `"""build_dcf_config should resolve sector betas, fetch peers, and call build_config."""` to:

```python
    """build_dcf_config should resolve sector betas and call build_config with no auto-selected peers."""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test_mcp_server.py::test_build_dcf_config_tool -v`
Expected: FAIL — current impl calls `find_peers`/`fetch_peer_data` and passes non-empty peers.

- [ ] **Step 3: Remove peer lookup in the MCP impl**

In `mcp_server.py`, replace lines 167-177:

```python
    peers = []
    if sic_code and market_cap > 0:
        try:
            peer_tickers = gather_data.find_peers(
                sic_code=int(sic_code),
                target_ticker=ticker,
                target_market_cap=market_cap,
            )
            peers = gather_data.fetch_peer_data(peer_tickers)
        except Exception as e:
            logger.warning("Peer lookup failed: %s", e)
```

with:

```python
    # Peer auto-selection removed — peers are authored via the MCP.
    peers = []
```

- [ ] **Step 4: Run the MCP test to verify it passes**

Run: `python3 -m pytest test_mcp_server.py::test_build_dcf_config_tool -v`
Expected: PASS.

- [ ] **Step 5: Remove peer auto-selection in the CLI**

In `gather_data.py`, replace the CLI peer block (lines 3673-3687, the `# ── Step 6: Peer data ──` block):

```python
    # ── Step 6: Peer data ──
    peer_tickers = []
    auto_peers = args.auto_peers or (args.peers and args.peers.strip().lower() == "auto")

    if auto_peers:
        # Auto-discover peers from SIC code + market cap similarity
        peer_tickers = find_peers(
            sic_code=sic_code,
            target_ticker=ticker,
            target_market_cap=market_cap,
            n_peers=args.n_peers,
        )
    elif args.peers and args.peers.strip().lower() != "auto":
        peer_tickers = [t.strip().upper() for t in args.peers.split(",") if t.strip()]

    peers = fetch_peer_data(peer_tickers)
```

with:

```python
    # ── Step 6: Peer data ──
    # Peer auto-selection removed — only explicit --peers "A,B,C" is honoured.
    peer_tickers = []
    if args.peers and args.peers.strip().lower() != "auto":
        peer_tickers = [t.strip().upper() for t in args.peers.split(",") if t.strip()]

    peers = fetch_peer_data(peer_tickers)
```

- [ ] **Step 6: Remove the `--auto-peers` argparse option**

In `gather_data.py`, delete the `--auto-peers` argument definition (the `parser.add_argument("--auto-peers", ...)` block at line ~3528). If a `--n-peers`/`--n_peers` argument exists solely for `find_peers`, delete it too. Update the module docstring example at the top (lines ~9 and ~3512) that shows `python3 gather_data.py PANW --auto-peers` — remove those example lines.

- [ ] **Step 7: Delete the `find_peers` function and the auto-peer script**

In `gather_data.py`, delete the entire `def find_peers(...)` function (lines ~2137-2249 — from `def find_peers` up to but not including `def fetch_peer_data` at line 2250). Then:

```bash
git rm scripts/backfill_peers.py
```

- [ ] **Step 8: Remove the `find_peers` import in Streamlit and its run_analysis call**

In `streamlit_app.py`, remove `find_peers,` from the `from gather_data import (...)` block (line 40). Keep `fetch_peer_data,` (line 41). Then in `run_analysis`, replace the peer block at lines 8506-8521:

```python
                peer_tickers = find_peers(
                    ...
                )
                ...
                peers = fetch_peer_data(peer_tickers)
```

with:

```python
                # Peer auto-selection removed — no peers on a fresh analysis.
                peers = []
```

(Read the exact 8506-8521 span before editing; match the real indentation and any surrounding `status.write` lines, removing only the `find_peers`→`fetch_peer_data` peer-selection statements.)

- [ ] **Step 9: Verify nothing else references `find_peers`**

Run: `grep -rn "find_peers" --include=*.py . | grep -v node_modules`
Expected: no matches except possibly comments. If a live call remains, remove it the same way.

- [ ] **Step 10: Run the full suite + lint**

Run: `python3 -m ruff check . && python3 -m pytest tests/ test_mcp_server.py test_tastytrade_api.py test_ibkr_api.py -q`
Expected: ruff clean; all pass except the known pre-existing `test_fetch_dividend_history_full_5y_payer`.

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "Remove peer auto-selection (find_peers); keep manual peer-add

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Remove valuation-time autofills

**Files:**
- Delete: `auto_fetch.py`, `scripts/force_refresh_all.py`
- Modify: `mcp_server.py:66` (import), `mcp_server.py:248-250` and `312-314` (calls); `streamlit_app.py:658-663` (re-export) and `707-709` (calls); `valuation_lenses.py:36` (comment)
- Test: remove autofill tests in `tests/test_market_data.py`; remove monkeypatches in `tests/test_mcp_server_user_id.py:139-141`; add a no-autofill regression test in `tests/test_mcp_server_user_id.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `_calculate_multi_lens_valuation_impl` and `_refresh_all_valuations_impl` compute the summary from the loaded config only — no yfinance mutation of `valuation_inputs`/peers.

- [ ] **Step 1: Write the failing regression test**

Append to `tests/test_mcp_server_user_id.py`:

```python
def test_calculate_multi_lens_does_not_autofill(monkeypatch):
    """Valuation no longer reaches out to yfinance: the config handed to the
    orchestrator equals the loaded config plus valuation_summary — user-authored
    valuation_inputs are never mutated."""
    import mcp_server
    loaded = {
        "ticker": "TEST",
        "valuation_inputs": {"forward_eps": 7.77, "_auto_filled": []},
        "peers": [{"ticker": "PEER", "fwd_pe": 20.0}],
    }
    saved = {}
    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: object())
    monkeypatch.setattr(mcp_server.config_store, "load_config",
                        lambda *a, **k: dict(loaded))
    monkeypatch.setattr(mcp_server.config_store, "save_config",
                        lambda client, ticker, cfg, **k: saved.update(cfg))
    monkeypatch.setattr(mcp_server.valuation_lenses, "calculate_multi_lens_valuation",
                        lambda cfg, **k: {"weighted_fv_mid": 100.0})
    # auto_fetch must no longer exist as an attribute on mcp_server
    assert not hasattr(mcp_server, "auto_fetch")

    mcp_server._calculate_multi_lens_valuation_impl("TEST", user_id="u")
    assert saved["valuation_inputs"] == {"forward_eps": 7.77, "_auto_filled": []}
    assert saved["peers"] == [{"ticker": "PEER", "fwd_pe": 20.0}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_mcp_server_user_id.py::test_calculate_multi_lens_does_not_autofill -v`
Expected: FAIL — `mcp_server` still imports `auto_fetch`, so `hasattr(...)` is True.

- [ ] **Step 3: Remove autofill from the MCP server**

In `mcp_server.py`: delete the `import auto_fetch` line (66).

Then in `_calculate_multi_lens_valuation_impl`, replace the whole comment-block-plus-calls at lines 244-250:

```python
    # Auto-fetch yfinance market data + historical multiples before the
    # orchestrator. Matches Streamlit's _refresh_one. Best-effort: yfinance
    # failures don't block the lens computation.
    cfg.setdefault("ticker", ticker)
    auto_fetch.auto_fill_valuation_inputs(cfg)
    auto_fetch.auto_fill_peer_market_data(cfg)
    auto_fetch.auto_fill_dividend_inputs(cfg)
```

with:

```python
    # Valuation uses only what the config already holds — no yfinance autofill.
    cfg.setdefault("ticker", ticker)
```

Then in `_refresh_all_valuations_impl`, delete the three call lines at 312-314 (leave the `cfg = dict(loaded[ticker])` and `cfg.setdefault("ticker", ticker)` at 310-311 intact):

```python
        auto_fetch.auto_fill_valuation_inputs(cfg)
        auto_fetch.auto_fill_peer_market_data(cfg)
        auto_fetch.auto_fill_dividend_inputs(cfg)
```

- [ ] **Step 4: Remove autofill from Streamlit**

In `streamlit_app.py`: delete the 3-line explanatory comment (lines 657-659) and the re-export block that follows (lines 660-663):

```python
from auto_fetch import (
    auto_fill_dividend_inputs as _auto_fill_dividend_inputs,
    auto_fill_peer_market_data as _auto_fill_peer_market_data,
    auto_fill_valuation_inputs as _auto_fill_valuation_inputs,
)
```

Then in `_refresh_one` (lines 707-709) delete:

```python
        _auto_fill_valuation_inputs(cfg)
        _auto_fill_peer_market_data(cfg)
        _auto_fill_dividend_inputs(cfg)
```

leaving the surrounding `cfg = dict(cfgs[ticker])` / `cfg.setdefault(...)` / `summary = ...` intact.

- [ ] **Step 5: Delete the module, the script, and update the stale comment**

```bash
git rm auto_fetch.py scripts/force_refresh_all.py
```

In `valuation_lenses.py:36`, update the comment line that references `scripts/force_refresh_all.py` (e.g. change `# - scripts/force_refresh_all.py (_counted — derives keys only)` to drop the dead reference, or remove the line if it only names that script).

- [ ] **Step 6: Remove obsolete autofill tests**

In `tests/test_market_data.py`, delete these test functions entirely (they test the removed autofill layer):

```
test_auto_fill_inputs_populates_empty
test_auto_fill_inputs_respects_user_set_value
test_auto_fill_inputs_overwrites_previous_auto_value
test_auto_fill_inputs_doesnt_overwrite_with_none
test_auto_fill_inputs_fetched_at_always_set
test_auto_fill_dividend_inputs_full
test_auto_fill_dividend_inputs_respects_user_override
test_auto_fill_dividend_inputs_non_payer_writes_zeros
test_auto_fill_peer_populates_empty
test_auto_fill_peer_respects_user_set_values
test_auto_fill_peer_refreshes_previously_auto_filled
test_auto_fill_peer_skips_invalid_entries
test_refresh_one_calls_auto_fill_before_orchestrator
test_auto_fill_inputs_includes_historical_multiples
test_auto_fill_inputs_writes_historical_fwd_pe
test_auto_fill_inputs_low_confidence_propagated_to_inputs
```

Keep the `test_fetch_historical_multiples_*` and `test_fetch_dividend_history_*` tests — those cover the fetch primitives, which stay. If any deleted test was the only user of an `import auto_fetch` at the top of the file, remove that import too.

In `tests/test_mcp_server_user_id.py`, delete the three monkeypatch lines at 139-141:

```python
    monkeypatch.setattr(mcp_server.auto_fetch, "auto_fill_valuation_inputs", lambda c: None)
    monkeypatch.setattr(mcp_server.auto_fetch, "auto_fill_peer_market_data", lambda c: None)
    monkeypatch.setattr(mcp_server.auto_fetch, "auto_fill_dividend_inputs", lambda c: None)
```

- [ ] **Step 7: Run the new test + verify no dangling references**

Run: `python3 -m pytest tests/test_mcp_server_user_id.py::test_calculate_multi_lens_does_not_autofill -v`
Expected: PASS.

Run: `grep -rn "auto_fetch\|auto_fill_\|force_refresh_all" --include=*.py . | grep -v node_modules`
Expected: no live references (comments referencing the concept are acceptable only if they no longer name a deleted symbol/file).

- [ ] **Step 8: Full suite + lint**

Run: `python3 -m ruff check . && python3 -m pytest tests/ test_mcp_server.py test_tastytrade_api.py test_ibkr_api.py -q`
Expected: ruff clean; all pass except the known pre-existing `test_fetch_dividend_history_full_5y_payer`.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "Remove valuation-time yfinance autofills; compute from config only

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Notes for the implementer

- Line numbers drift as you edit. Before each Edit, re-read the cited span and match on the anchor comment / function name, not the raw line number.
- The cloud handler `lazytheta-mcp-cloudrun/mcp_handler.py` calls `mcp_server._refresh_all_valuations_impl` — it needs no change (the impl it delegates to is fixed in Task 3). Its only `auto_fill` reference is inside a docstring string literal (line ~318); leave it or tidy the wording, but it is not executable code.
- After all three tasks, do a final manual smoke: `python3 -c "import gather_data, mcp_server, streamlit_app; print('imports OK')"` (streamlit_app needs the conftest session-state mock, so run it via `python3 -m pytest tests/test_watchlist_ui.py::test_scaffold_present -q` instead to confirm streamlit_app imports cleanly).
```
