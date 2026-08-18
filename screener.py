"""Quality screen: sustained return on capital, financed without net debt.

Two tests, both deliberately blunt:

  • average ROCE over the last 5-10 reported years is at or above the gate
  • the latest balance sheet carries no net debt

ROCE comes from scorecard_utils.roce_for_year — the same function the watchlist
and the robustness table use. A screener reporting 25% where the detail page
says 19% would be two answers to one question, and the reader has no way to
know which is the real one.

Pure arithmetic on a fundamentals dict; the caller does the fetching.
"""

import scorecard_utils

# Prasad's gate, and the one the robustness table already applies.
DEFAULT_MIN_ROCE = 20.0

# Fewer years than this is not a record. Three years of 40% says nothing about
# durability, and letting it through is the false positive this screen exists
# to avoid.
DEFAULT_MIN_YEARS = 5

# A decade of brilliance ending in 2014 says nothing about today.
DEFAULT_MAX_YEARS = 10


# A debt figure that collapses to near-nothing in one year is far more often a
# tag that stopped resolving than a balance sheet that was repaid. DPZ reports
# 4,934 and then 15 — Domino's carries about $5bn — and read literally that
# turns a leveraged company into a debt-free one, which is the worst mistake
# this screen can make. Below this fraction of the prior figure, and where the
# prior figure was material against the balance sheet, the series is not
# trusted.
_DEBT_COLLAPSE_RATIO = 0.20
_DEBT_MATERIAL_OF_ASSETS = 0.05


def debt_tag_suspect(fund, window=4):
    """True when the debt series falls off a cliff rather than declining.

    Shape is the only thing that separates the two cases from the data alone.
    Repaying debt is gradual — the series steps down. A tag that stops
    resolving drops to near-nothing in a single year and stays there, which is
    exactly DPZ: 4,934 for four years, then 15, then 15.

    Looking across the last few years rather than only the last two, because
    the cliff can sit a year back and the flat tail after it hides the drop.
    """
    debt = [v for v in (fund.get("total_debt") or []) if v is not None]
    assets = [v for v in (fund.get("total_assets") or []) if v is not None]
    if len(debt) < 2 or not assets:
        return False
    material = assets[-1] * _DEBT_MATERIAL_OF_ASSETS
    for prior, latest in zip(debt[-window - 1:-1], debt[-window:]):
        if prior > material and latest <= prior * _DEBT_COLLAPSE_RATIO:
            return True
    return False


def net_debt_latest(fund):
    """Debt minus cash and short-term investments, latest year, or None.

    None means "cannot tell" rather than zero: a filer that tags no cash has
    not told us it has none, and assuming zero would fail every company that
    simply did not break it out. A filer that tags no *debt* is different —
    that is the normal way a debt-free business reports, so it reads as zero.
    """
    def _latest(key):
        seq = [v for v in (fund.get(key) or []) if v is not None]
        return seq[-1] if seq else None

    cash = _latest("cash")
    investments = _latest("short_term_investments") or 0.0
    if cash is None:
        return None
    return (_latest("total_debt") or 0.0) - cash - investments


def screen_quality(fund, min_roce=DEFAULT_MIN_ROCE,
                   min_years=DEFAULT_MIN_YEARS, max_years=DEFAULT_MAX_YEARS):
    """Run the screen. Returns a dict; `passes` is the answer, `reason` the why.

    The average is what counts, not every single year — a cyclical is judged on
    its record rather than on its worst moment. Net debt is judged only on the
    latest year, so a business that has just deleveraged passes on where it
    stands now.
    """
    years = fund.get("years") or []
    n = len(years) or len(fund.get("operating_income") or [])

    # Most recent window first: index -1 is the latest year everywhere in this
    # codebase's fundamentals.
    roces = []
    for i in range(max(0, n - max_years), n):
        pct, _capped = scorecard_utils.roce_for_year(fund, i)
        if pct is not None:
            roces.append(pct)

    avg = (sum(roces) / len(roces)) if roces else None
    nd = net_debt_latest(fund)
    result = {
        "avg_roce": avg,
        "years_used": len(roces),
        "net_debt": nd,
        "passes": False,
        "reason": None,
    }

    if len(roces) < min_years:
        result["reason"] = "insufficient_history"
        return result
    if nd is None:
        result["reason"] = "no_balance_sheet"
        return result
    if debt_tag_suspect(fund):
        result["reason"] = "debt_tag_suspect"
        return result
    if avg < min_roce:
        result["reason"] = "roce_below_gate"
        return result
    if nd > 0:
        result["reason"] = "net_debt"
        return result

    result["passes"] = True
    return result


def compute_screener(universe, fetch, max_workers=5):
    """Screen every constituent of a universe. Returns rows plus a summary.

    `fetch` is injected (ticker -> fundamentals dict) so the arithmetic can be
    tested without EDGAR, and so the runner decides about caching and
    throttling rather than this function.

    A fetch failure is recorded as its own row with status "failed" — a name
    that vanishes silently looks identical to a name that failed the screen,
    and the two mean opposite things.
    """
    from collections import Counter
    from concurrent.futures import ThreadPoolExecutor

    constituents = universe.get("constituents") or []

    def one(c):
        ticker = c.get("ticker")
        base = {
            "ticker": ticker,
            "name": c.get("name"),
            "sector": c.get("gics_sector"),
            "indices": c.get("indices") or [],
            "passes": False,
        }
        try:
            fund = fetch(ticker)
        except Exception as e:
            base.update(status="failed", reason=f"fetch:{type(e).__name__}",
                        avg_roce=None, years_used=0, net_debt=None)
            return base
        r = screen_quality(fund)
        base.update(status="ok", reason=r["reason"],
                    avg_roce=r["avg_roce"], years_used=r["years_used"],
                    net_debt=r["net_debt"], passes=r["passes"])
        return base

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        rows = list(pool.map(one, constituents))

    per_index: dict = {}
    for r in rows:
        for idx in r["indices"]:
            entry = per_index.setdefault(idx, {"total": 0, "passes": 0})
            entry["total"] += 1
            entry["passes"] += 1 if r["passes"] else 0

    reasons = Counter(r["reason"] for r in rows if not r["passes"])
    return {
        "universe_as_of": universe.get("as_of"),
        "rows": rows,
        "summary": {
            "total": len(rows),
            "passes": sum(1 for r in rows if r["passes"]),
            "per_index": per_index,
            "reasons": dict(reasons),
        },
    }
