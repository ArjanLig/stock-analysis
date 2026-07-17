# Trading 212 read-only broker adapter (v1) — design

Date: 2026-07-17
Status: approved, ready for implementation plan

## Goal

Add Trading 212 as a third broker alongside Tastytrade and IBKR so the owner's
**General Invest** account positions and balance show up in the app's portfolio
views. **Read-only, live environment only.** No order placement, no paper/demo
environment.

Prior research: `Obsidian/lazytheta-vault/Idea - Trading 212 API integration.md`.

## Non-goals (v1)

- Order placement / any write operation.
- Demo / paper-trading environment (user does not use it).
- Dividend history, cash-transaction history, trade history.
- Any options-derived metric (greeks, margin, net-liq time series) — T212 is
  equity/ETF only and exposes no equivalent.
- Multi-user broker credentials — the broker side of the app is single-user
  (see `broker_adapter.py:55`), so no per-user isolation is needed.

## Architecture — follows the existing broker pattern

Three layers, mirroring Tastytrade / IBKR:

### `t212_api.py` (new)
Read-only REST client for the Trading 212 Public API (beta).

- Base URL: `https://live.trading212.com/api/v0` (live only; no env toggle).
- Auth: HTTP Basic, `Authorization: Basic base64(API_KEY:API_SECRET)`. Static
  key+secret, no OAuth/refresh chain.
- **Throttled sequential requests.** T212 rate limits are strict and per-account
  (`account/summary` 1 req / 5s; `positions` 1 req / 1s). The client issues
  requests sequentially, reads `x-ratelimit-*` response headers, and on HTTP 429
  backs off using the header's retry hint then retries (bounded retries). It must
  NOT be called from the app's `ThreadPoolExecutor(max_workers=6)` fan-out.
- Implements only what T212 supports:
  - `fetch_portfolio_data()` → `(cost_basis: dict, account_id: str)`
  - `fetch_account_balances()` → balances dict

### `broker_adapter.py` (edit)
- Add a `"t212"` branch to every routed function. Supported functions call
  `t212_api`; the options-only functions return a neutral empty value for
  `"t212"` (see Gap handling).
- Extend `get_active_broker()` auto-detect and `has_active_broker()` to recognise
  a connected T212 credential (`st.session_state["t212_credentials"]`).
- Add `_get_t212_creds()` using the same module-level cache pattern as
  `_get_refresh_token()` (`broker_adapter.py:56-77`), so worker threads that lack
  a Streamlit `ScriptRunContext` can still resolve the credentials.

### UI (edit `streamlit_app.py`)
- Add Trading 212 as a third option on the "Connect your Broker" page: a form for
  API key + secret, with a "read-only key" note.
- Add T212 to the broker switcher (around `streamlit_app.py:8665`).
- Load persisted credentials into `st.session_state["t212_credentials"]` alongside
  the existing broker credential loads (near `streamlit_app.py:2393`).

## Data normalisation — T212 → existing contract

`fetch_portfolio_data()` must return the same shape TT/IBKR return:
`(cost_basis_dict, account_id)`, where `cost_basis_dict` is keyed by clean ticker.

Built from `GET /equity/positions`:

| App field (per ticker)        | T212 source                                    |
|-------------------------------|------------------------------------------------|
| `shares_held`                 | `quantity`                                     |
| `cost_per_share`              | `averagePricePaid`                             |
| `equity_cost` / `adjusted_cost` | `totalCost` (fallback `quantity × averagePricePaid`) |
| `total_pl`                    | `unrealizedProfitLoss`                          |
| `option_pl`, `dividends`      | `0`                                            |
| `total_credits`, `total_debits` | `0` (no trade history in v1)                  |
| `trades`, `wheels`            | `[]`                                            |

`account_id` from `GET /equity/account/info`.

`fetch_account_balances()` from `GET /equity/account/summary` → the existing
balances dict (cash / invested / total value), matching the field names the
portfolio view already consumes from TT/IBKR.

### Ticker normalisation (gotcha)
T212 positions use instrument codes (e.g. `AAPL_US_EQ`), not bare tickers. Map to
a clean symbol (`AAPL`) by stripping the `_<EXCHANGE>_EQ` suffix. Where the suffix
convention does not yield a usable US symbol, fall back to the instruments
endpoint (ISIN / shortName) — but keep v1 to suffix-stripping unless a real case
needs more. This mapping is pinned by tests.

## Gap handling (equity-only broker)

For `active_broker == "t212"`, these adapter functions return a neutral empty
value of the SAME type an empty TT/IBKR result would produce, so existing panels
render empty / "n.v.t." instead of crashing:

- `fetch_portfolio_greeks`, `fetch_greeks_and_bwd`, `fetch_beta_weighted_delta`
- `fetch_margin_requirements`, `fetch_margin_for_position`, `fetch_margin_interest`
- `fetch_net_liq_history`
- `fetch_option_chain`
- `fetch_yearly_transfers` (dividends/transfers — out of v1 scope)

The exact empty shapes are taken from what each corresponding TT/IBKR function
returns on an empty account, verified during implementation.

Broker-independent shared functions (`fetch_current_prices`,
`fetch_ticker_profiles`, benchmark returns) are unchanged and keep routing to
`tastytrade_api`.

## Credentials & security

- Read-only API key + secret, persisted in **Supabase** via the existing
  `save_credential` / `load_credential` mechanism (keys `t212_api_key`,
  `t212_api_secret`), the same store used for the Tastytrade refresh token and
  IBKR credentials.
- Loaded into `st.session_state["t212_credentials"]` at startup next to the other
  broker credential loads.
- The connect form instructs the user to generate a **read-only** key in the T212
  app (Settings → API (Beta)); optional IP restriction is theirs to set.
- No secrets in code (CLAUDE.md rule 3).

## Testing

New `test_t212_api.py` in the style of `test_ibkr_api.py` / `test_tastytrade_api.py`
— fully offline, mocked HTTP, no network or real credentials:

- Basic-auth header construction from key+secret.
- Throttle + 429 retry using `x-ratelimit-*` / retry hint headers.
- `positions` → normalised `cost_basis` dict, including `AAPL_US_EQ` → `AAPL`.
- `account/summary` → balances dict.
- Adapter `"t212"` branch returns the correct neutral empties for the gap
  functions.

Must pass (CLAUDE.md rule 2) and lint clean (`ruff`, rule 1).

## Open questions / risks

- **Beta instability**: T212's auth schema changed recently (old beta = single
  token header; now key+secret). Community libs may target the old schema — do
  not rely on them blindly; implement against the current docs.
- Confirm the exact JSON field names on `positions` / `summary` against the live
  API during implementation (docs are the source of truth).
- Deploy path: Streamlit Cloud from GitHub `main` — this lands via a normal
  merge to `main` once implemented and verified.

## References

- https://docs.trading212.com/api
- https://docs.trading212.com/api/positions/getpositions
- https://docs.trading212.com/api/section/rate-limiting
- https://helpcentre.trading212.com/hc/en-us/articles/14584770928157-Trading-212-API-key
- `Obsidian/lazytheta-vault/Idea - Trading 212 API integration.md`
