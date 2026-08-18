"""Run the quality screen over the index universe and store one snapshot.

Runs locally, like the champions batch: EDGAR is reachable here and the page
only reads snapshots. Needs SUPABASE_URL + SUPABASE_SERVICE_KEY in the env.

    python3 scripts/run_screener.py
"""
import contextlib
import io
import json
import os
import pathlib
import sys
from datetime import UTC, datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from supabase import create_client

import gather_data
from screener import compute_screener


def _fetch(ticker):
    return gather_data.fetch_fundamentals(ticker, n_years=10)


def main():
    universe = json.loads(
        pathlib.Path("data/champions_universe.json").read_text())
    print(f"universum {universe['as_of']}: {universe['count']} namen")

    done = {"n": 0}

    def fetch(t):
        out = _fetch(t)
        done["n"] += 1
        if done["n"] % 25 == 0:
            print(f"  {done['n']} opgehaald...", file=sys.stderr)
        return out

    # One redirect around the whole batch: fetch_fundamentals narrates every
    # filing, and a per-thread redirect_stdout swaps the global stdout — not
    # thread-safe, and it swallowed the final summary on the first run.
    with contextlib.redirect_stdout(io.StringIO()):
        result = compute_screener(universe, fetch=fetch, max_workers=5)
    s = result["summary"]
    print(f"\n{s['total']} gescreend, {s['passes']} geslaagd")
    for idx, e in sorted(s["per_index"].items()):
        print(f"  {idx:10} {e['passes']:>3} van {e['total']}")
    print("  afgevallen:", s["reasons"])

    sb = create_client(os.environ["SUPABASE_URL"],
                       os.environ["SUPABASE_SERVICE_KEY"])
    sb.table("screener_snapshots").insert({
        "computed_at": datetime.now(UTC).isoformat(),
        "universe_as_of": result["universe_as_of"],
        "summary": s,
        "rows": result["rows"],
    }).execute()
    print("snapshot opgeslagen")


if __name__ == "__main__":
    main()
