# Merging a position held at two brokers — design

Date: 2026-08-27
Status: approved

## Problem

NVDA sits at both Tastytrade and Trading 212, so the Portfolio page shows it
twice. Economically it is one position: one company, one holding, one average
price paid. Two rows make the weight look wrong, the cost basis look like two
different answers, and the table longer than the portfolio is.

This is not about NVDA. Any ticker held at both brokers gets the same
treatment — the rule groups on the symbol and knows no ticker names.

## What exists today

`broker_adapter.fetch_all_portfolio_data` keeps rows per broker on purpose, and
says why:

> Positions stay separate per broker. Holding the same ticker at two brokers
> mid-transfer is a real state, and blending the two cost bases would print a
> purchase price that was never paid; two rows is what actually happened.

On a collision both keys take a broker suffix — `NVDA (Tastytrade)` next to
`NVDA (Trading 212)` — and each row carries `symbol` (the bare ticker) for
price, logo and config lookups.

Half of that reasoning holds and half does not. "A purchase price that was
never paid" is true of every average cost basis, including one built from two
buys at the same broker; it is what an average is. What genuinely matters is
the other half: you have to be able to lay a position next to your broker's own
statement and see the same number. Merging everywhere would take that away.

## Decision

Merge for display, per view, and leave the data alone.

- `fetch_all_portfolio_data` is **unchanged**. It stays the source of truth,
  with one row per broker. Merging is a display choice and belongs on the page.
- The Portfolio and Cost Basis pages merge **only when the broker view is
  "Overview"**. Both already share `_broker_view_control`, so both get the same
  rule. A broker tab keeps filtering exactly as it does now, which is what
  makes checking against a statement still possible.
- Results is untouched. It sums totals, and a sum does not care how the rows
  were grouped.

## The merge

A pure function in `portfolio_metrics.py`:

```python
def merge_by_symbol(positions: dict) -> dict
```

Rows sharing a `symbol` collapse into one, keyed by the bare ticker, so the
suffix disappears with the split that caused it.

**Summed:** `shares_held`, `equity_cost`, `option_pl`, `total_pl`,
`dividends`, `total_credits`, `total_debits`.

**Trades:** concatenated and sorted by date. FIFO then runs across the whole
holding rather than per broker, which is the right answer once the two are
being called one position.

**Recomputed from the sums, with the formulas already in use:**

| field | from |
|---|---|
| `adjusted_cost` | `equity_cost + option_pl` |
| `cost_per_share` | `adjusted_cost / shares_held`, 0.0 when flat |
| `wheels` | `trade_utils.detect_wheels(merged trades)` |
| `market_value` | `current_price × shares_held` |
| `total_pl_real` | `total_pl + market_value` |

Recomputing rather than adding matters for `cost_per_share`: adding two
averages gives a number that is not an average of anything.

**`broker`:** the contributing names joined — `"Tastytrade + Trading 212"`.

**`isin`, `broker_price`, `current_price`, `previous_close`:** first non-empty
value. The rows describe the same instrument, so these agree or one side simply
has nothing.

**`currency`, `fx_rate`:** kept only when every row agrees, dropped otherwise. A
blended exchange rate is a number that means nothing, and absent beats wrong.

## Ordering

The merge runs after `_load_portfolio_data`, so quotes are already attached and
`market_value` can be recomputed from a price both rows share.

## Tests

In `test_portfolio_metrics.py`:

- two brokers' shares and money add up
- merged trades come out in date order regardless of input order
- a ticker at one broker passes through untouched, keys included
- the broker suffix is gone from the merged key
- `cost_per_share` is recomputed, not averaged — two different averages in,
  the weighted one out
- `fx_rate` survives when the rows agree and is dropped when they do not
- options at one broker only still produce `wheels` on the merged row
- a flat position (zero shares) does not divide by zero

## Consequences

The displayed cost basis for a doubled ticker changes: one weighted average
where there were two. That is the number that answers "what did I pay for my
NVDA on average", but it is not what the page shows today, and it will look
like a change to a figure the user knows.

The broker tabs are now the only place a position can be checked against a
statement. That was already true for every other figure on the page; it is now
true for cost basis as well.
