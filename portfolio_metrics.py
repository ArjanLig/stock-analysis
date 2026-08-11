"""Portfolio-level deployment figures: how much is invested, how much is left.

Separate from the page that renders it so the arithmetic can be tested without
a Streamlit runtime — these numbers drive a "do I have anything left to buy
with" decision, and being wrong is worse than being absent.
"""

# A position within this fraction of its target counts as full. 4.7% of the
# portfolio against a 5% target is a full position in every sense that matters,
# and listing it as needing a top-up would bury the names that genuinely have
# room.
FILL_BAND = 0.90

# One full position as a percent of the portfolio, until the user says
# otherwise. 5% is twenty positions — enough concentration to matter, few
# enough to follow.
DEFAULT_TARGET_POS_PCT = 5.0


def has_option_legs(trades):
    """True when this ticker has ever had an option written against it.

    A wheel is shares plus options. detect_wheels returns a cycle for any share
    position — a plain buy sits in an "active" one — so a cycle is not evidence
    of a wheel. Without this, an outright purchase like NFLX or MSFT was given
    an adjusted basis and a premium column describing a trade never made.
    """
    return any("Option" in (t.get("instrument_type") or "") for t in trades)


def _equity_lots(trades):
    """Yield (trade, quantity, price, is_buy) for real equity trades.

    Dividends and other cash movements arrive as Equity rows with no quantity.
    They buy and sell nothing, and letting them through is how a dividend debit
    ended up raising the cost basis of shares it never bought, while a dividend
    credit was booked as equity profit that the dividend total already counted.
    """
    for t in trades:
        if t.get("instrument_type") != "Equity":
            continue
        if (t.get("type") or "") == "Money Movement":
            continue
        qty = t.get("quantity") or 0.0
        if not qty:
            continue
        price = t.get("price") or (abs(t.get("net_value") or 0.0) / qty)
        if (t.get("type") or "") == "Receive Deliver":
            is_buy = (t.get("net_value") or 0.0) < 0      # assignment in
        else:
            is_buy = "Buy" in (t.get("action") or "")
        yield t, qty, price, is_buy


def fifo_realized(trades):
    """Realized P/L per equity sale, oldest lot first.

    Returns [{"date", "quantity", "realized"}] in trade order.

    FIFO because that is the lot relief the broker applied: Tastytrade booked
    IBIT's August sale at -388.10, and a running-average walk made it -321.94.
    Two numbers for one sale means the app cannot be reconciled against the
    statement it sits next to.

    A sale with no lot to match against realizes nothing rather than booking
    its whole proceeds as profit — history can start mid-position, and a
    missing purchase is not a gain.
    """
    lots = []      # [remaining_qty, price]
    sales = []
    for t, qty, price, is_buy in _equity_lots(trades):
        if is_buy:
            lots.append([qty, price])
            continue
        remaining = qty
        cost = 0.0
        matched = 0.0
        while remaining > 0 and lots:
            take = min(lots[0][0], remaining)
            cost += take * lots[0][1]
            lots[0][0] -= take
            remaining -= take
            matched += take
            if lots[0][0] <= 0:
                lots.pop(0)
        if not matched:
            continue
        # Proceeds for the shares actually matched, so a partially matched sale
        # is not credited with cash from shares it could not account for.
        proceeds = abs(t.get("net_value") or 0.0) * (matched / qty)
        sales.append({
            "date": t.get("date"),
            "quantity": matched,
            "realized": proceeds - cost,
        })
    return sales


def open_lots(trades):
    """The share lots still held, oldest first: [{quantity, price, date}].

    Same FIFO walk as everything else here, but keeping each lot's purchase
    date — relative performance is measured from the day the money went in, and
    a lot without its date cannot be compared to anything.
    """
    lots = []
    for t, qty, price, is_buy in _equity_lots(trades):
        if is_buy:
            lots.append({"quantity": qty, "price": price, "date": t.get("date")})
            continue
        remaining = qty
        while remaining > 0 and lots:
            take = min(lots[0]["quantity"], remaining)
            lots[0]["quantity"] -= take
            remaining -= take
            if lots[0]["quantity"] <= 0:
                lots.pop(0)
    return lots


def _close_on_or_before(closes, day):
    """The index close for `day`, or the nearest earlier one.

    Buy on a Saturday and there is no close for that date; skipping the lot
    would drop it from the comparison without saying so.
    """
    if not closes or day is None:
        return None
    if day in closes:
        return closes[day]
    earlier = [d for d in closes if d <= day]
    return closes[max(earlier)] if earlier else None


def relative_performance(lots, current_price, index_closes, today):
    """How the position has done against the index, per lot, money-weighted.

    Each lot faced its own stretch of market, so a January purchase and a
    March top-up are compared to different index windows and then weighted by
    cost. Averaging the two returns equally would let a small late top-up
    decide the verdict on a large long-held position.

    Price return on both sides — dividends are counted in neither, so the
    comparison stays like-for-like.

    A lot older than the index history we hold is reported through
    `uncovered_cost` rather than anchored to the oldest close available, which
    would understate the index and hand the position free alpha.
    """
    cost = index_value = weighted_days = uncovered = 0.0
    for lot in lots:
        lot_cost = lot["quantity"] * lot["price"]
        start = _close_on_or_before(index_closes, lot.get("date"))
        if not start or not lot.get("date"):
            uncovered += lot_cost
            continue
        cost += lot_cost
        index_value += lot_cost * (index_closes[max(index_closes)] / start)
        weighted_days += lot_cost * (today - lot["date"]).days

    if cost <= 0:
        return {"position_return": None, "index_return": None, "alpha": None,
                "days_held": None, "uncovered_cost": uncovered}

    shares = sum(lot["quantity"] for lot in lots
                 if _close_on_or_before(index_closes, lot.get("date")))
    position_return = (current_price * shares / cost - 1) * 100
    index_return = (index_value / cost - 1) * 100
    return {
        "position_return": position_return,
        "index_return": index_return,
        "alpha": position_return - index_return,
        "days_held": round(weighted_days / cost),
        "uncovered_cost": uncovered,
    }


def held_share_cost(trades):
    """FIFO cost of the shares still held: (total_cost, shares).

    Summing every buy in the cycle answers "what did I pay for everything I
    ever bought", which stops being the purchase price the moment part of the
    position is sold. IBIT — assigned 100 at 56.00, added 20 at 35.89, sold 20
    at 36.60 — came out at 52.65 for 120 shares of which 20 were gone. FIFO
    retires the oldest lot first and gives 51.98, matching the broker.
    """
    lots = []  # [remaining_qty, price]
    for _t, qty, price, is_buy in _equity_lots(trades):
        if is_buy:
            lots.append([qty, price])
            continue
        # A sale retires the oldest lots. History can start mid-position, so a
        # sale with nothing to match against simply finds no lot rather than
        # driving the holding negative.
        remaining = qty
        while remaining > 0 and lots:
            take = min(lots[0][0], remaining)
            lots[0][0] -= take
            remaining -= take
            if lots[0][0] <= 0:
                lots.pop(0)

    shares = sum(lot[0] for lot in lots)
    cost = sum(lot[0] * lot[1] for lot in lots)
    return cost, shares


def display_basis(net_cash_per_share):
    """Turn a signed per-share cash flow into the price a column should show.

    Cost basis is carried through the app as cash: buying shares is money out,
    so it is negative, and premiums collected push it back up. A column headed
    "Wheel Basis" wants the price, which is the negation.

    Negation, not abs(): once premiums exceed what the shares cost, the basis
    really is below zero — you have been paid to hold them. abs() would print
    that as a cost and invert the meaning of the one case worth spotting.
    """
    return -net_cash_per_share


def compute_deployment(held, net_liq, cash, target_pct,
                       prices=None, buy_prices=None):
    """Summarise how much of the portfolio is committed and what is left.

    held        {key: position} with "market_value" and optionally "symbol"
    net_liq     total account value across brokers
    cash        actual cash across brokers — NOT net_liq minus market value.
                At a margin broker the remainder also carries short option
                value and any borrowing, so calling it dry powder would promise
                money that cannot be spent.
    target_pct  size of one full position, in percent of net_liq
    prices      {symbol: current price}, for the below-buy-price count
    buy_prices  {symbol: watchlist buy price}; names absent from it are left
                out of the count entirely — no valuation is not the same as
                "not a buy"

    Returns a dict of plain numbers; the page formats them.
    """
    invested = sum(d.get("market_value") or 0.0 for d in held.values())
    target = net_liq * (target_pct / 100.0) if net_liq > 0 else 0.0

    full_count = 0
    partial = []
    for key, data in held.items():
        mv = data.get("market_value") or 0.0
        if target <= 0 or mv >= target * FILL_BAND:
            full_count += 1
            continue
        partial.append({
            "ticker": data.get("symbol") or key,
            "key": key,
            "market_value": mv,
            # Gap to target, floored at zero: an oversized winner needs no
            # cash and must not read as a credit against another name's
            # shortfall.
            "gap": max(target - mv, 0.0),
        })
    partial.sort(key=lambda p: p["gap"], reverse=True)

    top_up_cost = sum(p["gap"] for p in partial)
    leftover = cash - top_up_cost
    new_positions = (leftover / target) if target > 0 and leftover > 0 else 0.0

    below_buy, valued_count = [], 0
    prices = prices or {}
    buy_prices = buy_prices or {}
    for key, data in held.items():
        symbol = data.get("symbol") or key
        buy = buy_prices.get(symbol)
        price = prices.get(symbol)
        if not buy or not price:
            continue
        valued_count += 1
        if price < buy:
            below_buy.append(symbol)

    return {
        "invested": invested,
        "deployed_pct": (invested / net_liq * 100.0) if net_liq > 0 else 0.0,
        "dry_powder": cash,
        "dry_powder_pct": (cash / net_liq * 100.0) if net_liq > 0 else 0.0,
        "target": target,
        "full_count": full_count,
        "partial": partial,
        "top_up_cost": top_up_cost,
        "cash_covers_top_ups": cash >= top_up_cost,
        "new_positions_affordable": round(new_positions, 1),
        # Where spending every available dollar on the names already owned
        # would leave you. The point of the card: whether the powder is really
        # dry or already spoken for.
        "fully_deployed_pct": (
            min((invested + cash) / net_liq * 100.0, 100.0) if net_liq > 0 else 0.0
        ),
        "below_buy": below_buy,
        "valued_count": valued_count,
    }
