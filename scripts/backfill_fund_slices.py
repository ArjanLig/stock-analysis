"""Store the watchlist's EDGAR slice on every config.

The watchlist row needs three numbers per ticker — FCF yield, the quality
metric and its average — and computed them from a ~5 MB companyfacts file per
name. For a 77-ticker list that is 380 MB and about 13 seconds, and because
st.cache_data lives in the container's memory, every Streamlit Cloud restart
threw the cache away and the next visitor paid it again.

This writes the fourteen series those calculations actually read onto each
config, roughly a kilobyte per ticker. The page then does the same arithmetic
on the same numbers without touching the network.

Runs locally, like the screener batch: EDGAR is not reliably reachable from
Streamlit Cloud.

    SUPABASE_URL=... SUPABASE_SERVICE_KEY=... \
        python3 scripts/backfill_fund_slices.py [--user-id UUID] [--dry-run]
"""

import argparse
import contextlib
import io
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config_store
import gather_data
from scorecard_utils import slim_fundamentals, slice_is_usable

MAX_WORKERS = 6


def build_slice(ticker):
    """(ticker, slice, error). EDGAR chatter is captured, not printed."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            fund = gather_data.fetch_fundamentals(ticker, n_years=10)
        return ticker, slim_fundamentals(fund), None
    except Exception as e:
        return ticker, None, f"{type(e).__name__}: {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user-id", default=None,
                    help="Only this user's configs; default is every config "
                         "the service key can see.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute and report, write nothing.")
    ap.add_argument("--only-missing", action="store_true",
                    help="Skip tickers that already carry a usable slice.")
    args = ap.parse_args()

    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if not (url and key):
        sys.exit("SUPABASE_URL en SUPABASE_SERVICE_KEY moeten gezet zijn.")

    from supabase import create_client
    client = create_client(url, key)

    # Without ai_notes: this only needs the ticker list and any existing
    # slice, and the prose is 79% of the payload. Nothing here writes a
    # loaded config back — save_config gets the one key that changed.
    cfgs = config_store.load_all_configs(client, user_id=args.user_id,
                                         include_ai_notes=False)
    todo = [t for t, c in cfgs.items()
            if not (args.only_missing and slice_is_usable((c or {}).get("fund_slice")))]
    print(f"{len(cfgs)} configs, {len(todo)} te verwerken", file=sys.stderr)
    if not todo:
        return

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        results = list(pool.map(build_slice, todo))

    geschreven = overgeslagen = mislukt = 0
    for ticker, sl, err in results:
        if err or not slice_is_usable(sl):
            mislukt += 1
            print(f"  MISLUKT {ticker}: {err or 'geen bruikbare reeksen'}",
                  file=sys.stderr)
            continue
        if args.dry_run:
            overgeslagen += 1
            continue
        try:
            # Pass only what changes: save_config keeps every key the caller
            # leaves out, so this cannot disturb the rest of the config.
            config_store.save_config(client, ticker, {"fund_slice": sl},
                                     user_id=args.user_id)
            geschreven += 1
        except Exception as e:
            mislukt += 1
            print(f"  SCHRIJFFOUT {ticker}: {e}", file=sys.stderr)

    print(f"\ngeschreven: {geschreven}, mislukt: {mislukt}"
          + (f", dry-run overgeslagen: {overgeslagen}" if args.dry_run else ""),
          file=sys.stderr)


if __name__ == "__main__":
    main()
