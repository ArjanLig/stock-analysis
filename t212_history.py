"""Rebuild a Trading 212 account-value curve from its primitives.

T212 exposes no net-liquidating-value history — the endpoint simply does not
exist, which is why broker_adapter returned an empty list for it. But it does
expose every fill, every cash movement and every dividend, and with daily
closes that is enough to compute what the account was worth on any past day.

Everything is computed in the ACCOUNT's currency and converted to USD at each
day's own rate. Converting at today's rate instead would show a euro account a
gain or loss its currency made after the fact — or hide one it made at the
time. For a USD-listed holding the two conversions cancel, as they should.

Pure arithmetic, no network: the caller supplies the fills, the movements, the
closes and the FX series.
"""

from datetime import timedelta


def _on_or_before(series, day):
    """The value for `day`, or the most recent one before it.

    Weekends, holidays and a missing print are all the same thing here: the
    account existed that day, so the curve should not have a hole in it.
    """
    if not series:
        return None
    if day in series:
        return series[day]
    earlier = [d for d in series if d <= day]
    return series[max(earlier)] if earlier else None


def reconstruct_net_liq(fills, cash_movements, closes, fx, start, end):
    """Return ([{"time", "close"}], unpriced_symbols) in USD, one point a day.

    fills            [{date, symbol, quantity, is_buy, native_price,
                      native_currency, wallet_net_value}] — wallet_net_value is
                     the cash the account actually moved, signed, in account
                     currency.
    cash_movements   [{date, amount, type}] in account currency.
    closes           {symbol: {date: close}} in the instrument's currency.
    fx               {date: account-currency → USD rate}.

    A holding with no closes is carried at what it cost rather than valued at
    zero, and its symbol is returned. Dropping it would understate the account
    by a whole position; pretending to price it would be worse.
    """
    if not fills and not cash_movements:
        return [], []

    unpriced = sorted({
        f["symbol"] for f in fills
        if not closes.get(f["symbol"])
    })

    series = []
    day = start
    while day <= end:
        rate = _on_or_before(fx, day)
        if rate is None:
            day += timedelta(days=1)
            continue

        # Cash in account currency: what was paid in, plus what every fill did
        # to the wallet. Interest and fees ride along in the movements.
        cash = sum(m["amount"] for m in cash_movements if m["date"] <= day)
        cash += sum(f["wallet_net_value"] for f in fills if f["date"] <= day)

        # Positions, in account currency.
        holdings = {}
        cost = {}
        for f in fills:
            if f["date"] > day:
                continue
            qty = f["quantity"] if f["is_buy"] else -f["quantity"]
            holdings[f["symbol"]] = holdings.get(f["symbol"], 0.0) + qty
            cost[f["symbol"]] = cost.get(f["symbol"], 0.0) - f["wallet_net_value"]

        positions = 0.0
        for symbol, qty in holdings.items():
            if abs(qty) < 1e-9:
                continue
            close = _on_or_before(closes.get(symbol) or {}, day)
            if close is None:
                # Carried at cost — see the docstring.
                positions += cost.get(symbol, 0.0)
                continue
            native = next((f["native_currency"] for f in fills
                           if f["symbol"] == symbol), "")
            value = qty * close
            # A close is quoted in the instrument's currency; the account is
            # not necessarily in it.
            positions += value / rate if native and native != "EUR" else value

        series.append({"time": day.isoformat(), "close": (cash + positions) * rate})
        day += timedelta(days=1)

    return series, unpriced


# Money you put in or took out. Interest, dividends and fees are returns ON the
# account, not transfers INTO it — counting them would flatter every
# deposit-adjusted return the Results page computes.
_TRANSFER_TYPES = ("DEPOSIT", "WITHDRAW", "WITHDRAWAL")


def yearly_transfers(cash_movements, fx):
    """Net deposits per year and month, in USD.

    Shape matches tastytrade_api.fetch_yearly_transfers:
    {year: {"total": float, "months": {month: float}}}.
    """
    out = {}
    for m in cash_movements:
        if (m.get("type") or "").upper() not in _TRANSFER_TYPES:
            continue
        rate = _on_or_before(fx, m["date"]) or 1.0
        amount = m["amount"] * rate
        year = out.setdefault(m["date"].year, {"total": 0.0, "months": {}})
        year["total"] += amount
        year["months"][m["date"].month] = (
            year["months"].get(m["date"].month, 0.0) + amount
        )
    return out


# T212's instrument code carries an exchange for US listings and often nothing
# for the rest — the Amundi ETF is "WEBN1d_EQ", which strips to nothing usable.
# Where the exchange is known, use it; otherwise fall back to the listing
# currency, which narrows a European ETF to a couple of realistic venues.
_EXCHANGE_SUFFIX = {
    "US": "", "DE": "DE", "XETRA": "DE", "FRA": "F",
    "LSE": "L", "LON": "L", "AMS": "AS", "EPA": "PA", "PAR": "PA",
    "MIL": "MI", "MAD": "MC", "SWX": "SW", "STO": "ST", "CPH": "CO",
}

# Ordered by how likely a listing in that currency sits on that venue.
_CURRENCY_SUFFIXES = {
    "EUR": ["DE", "PA", "AS", "MI"],
    "GBP": ["L"], "GBX": ["L"], "CHF": ["SW"],
    "SEK": ["ST"], "DKK": ["CO"], "NOK": ["OL"], "CAD": ["TO"],
}


def yahoo_candidates(symbol, exchange, currency):
    """Yahoo tickers to try for a T212 instrument, best first.

    The bare symbol always leads: it is the only form that can be right for a
    US listing, and trying a suffix first risks resolving to a different
    company that happens to share the ticker abroad. An unknown currency gets
    no guesses at all — no candidate beats a wrong one.
    """
    out = [symbol]
    suffix = _EXCHANGE_SUFFIX.get((exchange or "").upper())
    if suffix:
        out.append(f"{symbol}.{suffix}")
    for s in _CURRENCY_SUFFIXES.get((currency or "").upper(), []):
        candidate = f"{symbol}.{s}"
        if candidate not in out:
            out.append(candidate)
    return out
