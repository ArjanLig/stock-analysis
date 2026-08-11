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
