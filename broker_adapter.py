"""
Broker Adapter — routes all broker API calls to the active broker backend.

Delegates to either tastytrade_api or ibkr_api based on
st.session_state["active_broker"]. Callers never pass refresh tokens
or credentials; the adapter handles that internally.
"""

import streamlit as st
import t212_api
import tastytrade_api


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def get_active_broker():
    """Return the name of the currently active broker.

    If not explicitly set, auto-detect based on which brokers are connected.
    """
    explicit = st.session_state.get("active_broker")
    if explicit:
        return explicit
    # Auto-detect: if only one broker is connected, use that one.
    # If more than one are connected the sidebar switcher should be shown so
    # the user picks explicitly; default to tastytrade until they do.
    has_tt = bool(st.session_state.get("tt_refresh_token"))
    has_ibkr = bool(st.session_state.get("ibkr_credentials"))
    has_t212 = bool(st.session_state.get("t212_credentials"))
    if has_t212 and not has_tt and not has_ibkr:
        return "t212"
    if has_ibkr and not has_tt:
        return "ibkr"
    if has_tt and not has_ibkr:
        return "tastytrade"
    # Multiple connected — default to tastytrade (sidebar switcher lets user change)
    return "tastytrade"


def has_active_broker():
    """Return True if the user has at least one broker connected."""
    return bool(
        st.session_state.get("tt_refresh_token")
        or st.session_state.get("ibkr_credentials")
        or st.session_state.get("t212_credentials")
    )


def _get_ibkr():
    """Lazy-import ibkr_api so the module loads even before ibkr_api.py exists."""
    import ibkr_api
    return ibkr_api


# Module-level cache so worker threads (ThreadPoolExecutor) can resolve the
# refresh token even though st.session_state is unreachable without a
# ScriptRunContext. Main-thread reads keep the cache fresh; worker reads fall
# back to it. Single-user app, so no cross-user contamination concern.
_TT_RT_CACHE: str | None = None


def _get_refresh_token():
    """Get the TastyTrade refresh token, working from main and worker threads.

    Main thread: reads st.session_state and refreshes the module cache.
    Worker thread: st.session_state silently returns None (no ScriptRunContext)
    so we fall back to the cache populated by an earlier main-thread call.
    Without this, ThreadPool callers get None and tastytrade_api falls back to
    the stale TASTYTRADE_REFRESH_TOKEN in st.secrets — TT immediately revokes
    the grant chain on that dead token.
    """
    global _TT_RT_CACHE
    try:
        rt = st.session_state.get("tt_refresh_token")
        if rt:
            _TT_RT_CACHE = rt
            return rt
    except Exception:
        pass
    return _TT_RT_CACHE


# Module-level cache mirroring _TT_RT_CACHE above, so worker threads can
# resolve T212 credentials too. Single-user app, no cross-user concern.
_T212_CREDS_CACHE: dict | None = None


def _get_t212_creds():
    """Get T212 credentials, working from main and worker threads (see _get_refresh_token)."""
    global _T212_CREDS_CACHE
    try:
        creds = st.session_state.get("t212_credentials")
        if creds:
            _T212_CREDS_CACHE = creds
            return creds
    except Exception:
        pass
    return _T212_CREDS_CACHE


# ---------------------------------------------------------------------------
# Routed broker-specific functions
# ---------------------------------------------------------------------------

def fetch_portfolio_data():
    if get_active_broker() == "t212":
        return t212_api.fetch_portfolio_data(_get_t212_creds())
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_portfolio_data()
    return tastytrade_api.fetch_portfolio_data(refresh_token=_get_refresh_token())


def fetch_account_balances():
    if get_active_broker() == "t212":
        return t212_api.fetch_account_balances(_get_t212_creds())
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_account_balances()
    return tastytrade_api.fetch_account_balances(refresh_token=_get_refresh_token())


def fetch_margin_requirements():
    # T212 has no margin (cash/no-leverage broker); no TT/IBKR equivalent applies.
    if get_active_broker() == "t212":
        return {}
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_margin_requirements()
    return tastytrade_api.fetch_margin_requirements(refresh_token=_get_refresh_token())


def fetch_margin_for_position(ticker, quantity):
    if get_active_broker() == "t212":
        return None
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_margin_for_position(ticker, quantity)
    return tastytrade_api.fetch_margin_for_position(
        ticker, quantity, refresh_token=_get_refresh_token()
    )


def fetch_net_liq_history(time_back="1y"):
    if get_active_broker() == "t212":
        # T212 has no history endpoint; the curve is rebuilt from fills, cash
        # movements and daily closes. See t212_history.
        return t212_api.fetch_net_liq_history(_get_t212_creds(), time_back)
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_net_liq_history(time_back=time_back)
    return tastytrade_api.fetch_net_liq_history(
        time_back=time_back, refresh_token=_get_refresh_token()
    )


def fetch_portfolio_greeks():
    # T212 is options-only-free (equities only for now); no Greeks to report.
    if get_active_broker() == "t212":
        return {"positions": [], "totals": {"delta": 0, "theta": 0, "gamma": 0, "vega": 0}}
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_portfolio_greeks()
    return tastytrade_api.fetch_portfolio_greeks(refresh_token=_get_refresh_token())


def fetch_greeks_and_bwd():
    if get_active_broker() == "t212":
        return fetch_portfolio_greeks(), fetch_beta_weighted_delta()
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_greeks_and_bwd()
    return tastytrade_api.fetch_greeks_and_bwd(refresh_token=_get_refresh_token())


def fetch_beta_weighted_delta():
    if get_active_broker() == "t212":
        return {"positions": [], "portfolio_bwd": 0, "spy_price": 0, "dollar_per_1pct": 0}
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_beta_weighted_delta()
    return tastytrade_api.fetch_beta_weighted_delta(refresh_token=_get_refresh_token())


def fetch_yearly_transfers():
    if get_active_broker() == "t212":
        return t212_api.fetch_yearly_transfers(_get_t212_creds())
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_yearly_transfers()
    return tastytrade_api.fetch_yearly_transfers(refresh_token=_get_refresh_token())


def fetch_margin_interest():
    if get_active_broker() == "t212":
        return {"current_month": 0, "ytd": 0, "total": 0, "monthly": {}}
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_margin_interest()
    return tastytrade_api.fetch_margin_interest(refresh_token=_get_refresh_token())


def fetch_option_chain(
    ticker,
    option_type="Put",
    min_dte=7,
    max_dte=60,
    num_strikes=8,
    fallback_price=0.0,
):
    if get_active_broker() == "t212":
        return {"underlying_price": fallback_price, "expirations": []}
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_option_chain(
            ticker,
            option_type=option_type,
            min_dte=min_dte,
            max_dte=max_dte,
            num_strikes=num_strikes,
            fallback_price=fallback_price,
        )
    return tastytrade_api.fetch_option_chain(
        ticker,
        option_type=option_type,
        min_dte=min_dte,
        max_dte=max_dte,
        num_strikes=num_strikes,
        fallback_price=fallback_price,
        refresh_token=_get_refresh_token(),
    )


def fetch_earnings_dates(tickers):
    if get_active_broker() == "t212":
        return {t: None for t in tickers}
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_earnings_dates(tickers)
    return tastytrade_api.fetch_earnings_dates(
        tickers, refresh_token=_get_refresh_token()
    )


# ---------------------------------------------------------------------------
# Multi-broker aggregation
# ---------------------------------------------------------------------------

BROKER_NAMES = {
    "tastytrade": "Tastytrade",
    "ibkr": "Interactive Brokers",
    "t212": "Trading 212",
}


def connected_brokers():
    """Return the connected brokers, in a stable display order."""
    out = []
    if st.session_state.get("tt_refresh_token"):
        out.append("tastytrade")
    if st.session_state.get("ibkr_credentials"):
        out.append("ibkr")
    if st.session_state.get("t212_credentials"):
        out.append("t212")
    return out


def _fetch_one(broker):
    if broker == "t212":
        return t212_api.fetch_portfolio_data(_get_t212_creds())
    if broker == "ibkr":
        return _get_ibkr().fetch_portfolio_data()
    return tastytrade_api.fetch_portfolio_data(refresh_token=_get_refresh_token())


def fetch_all_portfolio_data():
    """Return (cost_basis, account_id, failures) across every connected broker.

    Positions stay separate per broker. Holding the same ticker at two brokers
    mid-transfer is a real state, and blending the two cost bases would print a
    purchase price that was never paid; two rows is what actually happened. On
    a collision BOTH keys get the broker suffix — a bare "DECK" sitting next to
    "DECK (Trading 212)" reads as though the first row belonged to no broker.

    Because the dict key is therefore a display key, each row carries "symbol"
    (the bare ticker) for price, logo and config lookups.

    `failures` is [(broker_name, exception)] for brokers that could not be
    reached. Their rows are simply absent, so any total struck from this data
    is incomplete — the caller has to say so rather than present a smaller
    number as the truth.
    """
    per_broker, failures = {}, []
    for broker in connected_brokers():
        try:
            cb, acct = _fetch_one(broker)
        except Exception as e:
            failures.append((BROKER_NAMES[broker], e))
            continue
        per_broker[broker] = (cb or {}, acct)

    counts = {}
    for cb, _ in per_broker.values():
        for ticker in cb:
            counts[ticker] = counts.get(ticker, 0) + 1

    merged = {}
    for broker, (cb, _) in per_broker.items():
        name = BROKER_NAMES[broker]
        for ticker, data in cb.items():
            row = dict(data)
            row["broker"] = name
            row["symbol"] = ticker
            key = ticker if counts[ticker] == 1 else f"{ticker} ({name})"
            merged[key] = row

    active = get_active_broker()
    account_id = per_broker.get(active, (None, ""))[1]
    if not account_id and per_broker:
        account_id = next(iter(per_broker.values()))[1]
    return merged, account_id, failures


def fetch_all_balances():
    """Return ({broker_name: balances}, failures) across every connected broker.

    Same caveat as fetch_all_portfolio_data: a broker in `failures` contributes
    nothing, so any total struck from this is a floor, not the answer.
    """
    per_broker, failures = {}, []
    for broker in connected_brokers():
        try:
            if broker == "t212":
                bal = t212_api.fetch_account_balances(_get_t212_creds())
            elif broker == "ibkr":
                bal = _get_ibkr().fetch_account_balances()
            else:
                bal = tastytrade_api.fetch_account_balances(
                    refresh_token=_get_refresh_token()
                )
        except Exception as e:
            failures.append((BROKER_NAMES[broker], e))
            continue
        per_broker[BROKER_NAMES[broker]] = bal or {}
    return per_broker, failures


def fetch_all_net_liq():
    """Return (total, {broker_name: net_liq}, failures) across all brokers."""
    per_broker, failures = fetch_all_balances()
    values = {
        name: (bal.get("net_liquidating_value") or 0.0)
        for name, bal in per_broker.items()
    }
    return sum(values.values()), values, failures


# ---------------------------------------------------------------------------
# Shared functions (broker-independent, always route to tastytrade_api)
# ---------------------------------------------------------------------------

def fetch_current_prices(tickers):
    return tastytrade_api.fetch_current_prices(tickers)


def fetch_ticker_profiles(tickers):
    return tastytrade_api.fetch_ticker_profiles(tickers)


def fetch_benchmark_returns():
    return tastytrade_api.fetch_benchmark_returns()


def fetch_benchmark_monthly_returns():
    return tastytrade_api.fetch_benchmark_monthly_returns()


def fetch_sp500_yearly_returns():
    return tastytrade_api.fetch_sp500_yearly_returns()
