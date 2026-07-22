"""
Historical loser filter logic for the legacy screener.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from legacy.config_old import (
    LOOKBACK_POINTS,
    LOSER_MODE,
    LOSER_THRESHOLD_PCT,
    MAX_WORKERS,
    MIN_LOSER_TIMEFRAMES,
)
from legacy.data_fetcher_old import get_candle_change_at, get_pct_change_at


def check_symbol_losses(sym: str) -> Optional[dict]:
    """Check one symbol across all lookback timeframes (current price method)."""
    row = {"symbol": sym, "qualified": False}
    meets_threshold_count = 0
    valid_timeframes = 0

    for label, days_ago in LOOKBACK_POINTS:
        pct = get_pct_change_at(sym, days_ago)
        row[label] = pct
        if pct is not None:
            valid_timeframes += 1
            if pct <= LOSER_THRESHOLD_PCT:
                meets_threshold_count += 1

    if LOSER_MODE == "ALL":
        row["qualified"] = (meets_threshold_count == valid_timeframes) and (valid_timeframes > 0)
    elif LOSER_MODE == "MIN":
        row["qualified"] = meets_threshold_count >= MIN_LOSER_TIMEFRAMES
    else:
        row["qualified"] = meets_threshold_count > 0

    return row


def check_symbol_losses_old(sym: str) -> Optional[dict]:
    """Check one symbol using legacy daily candle-change method."""
    row = {"symbol": sym, "qualified": False}
    meets_threshold_count = 0
    valid_timeframes = 0

    for label, days_ago in LOOKBACK_POINTS:
        pct = get_candle_change_at(sym, int(days_ago))
        row[label] = pct
        if pct is not None:
            valid_timeframes += 1
            if pct <= LOSER_THRESHOLD_PCT:
                meets_threshold_count += 1

    if LOSER_MODE == "ALL":
        row["qualified"] = (meets_threshold_count == valid_timeframes) and (valid_timeframes > 0)
    elif LOSER_MODE == "MIN":
        row["qualified"] = meets_threshold_count >= MIN_LOSER_TIMEFRAMES
    else:
        row["qualified"] = meets_threshold_count > 0

    return row


def find_historical_losers(symbols: list) -> list:
    print(f"[2/4] Scanning historical % changes ({len(symbols)} tokens)...")
    print(f"      Timeframes: {', '.join(label for label, _ in LOOKBACK_POINTS)}")
    print(f"      Threshold : {LOSER_THRESHOLD_PCT}% at ANY point\n")

    results = []
    done = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures_map = {executor.submit(check_symbol_losses, sym): sym for sym in symbols}
        for future in as_completed(futures_map):
            done += 1
            try:
                res = future.result()
                if res and res["qualified"]:
                    results.append(res)
            except Exception:
                pass
            if done % 30 == 0 or done == len(symbols):
                print(f"      Progress: {done}/{len(symbols)} | Losers found: {len(results)}")

    print(f"\n      {len(results)} tokens qualified as historical losers\n")
    return results


def find_historical_losers_old(symbols: list) -> list:
    print(f"[2b/4] Scanning OLD method - 24h candle changes ({len(symbols)} tokens)...")
    print(f"       Timeframes: {', '.join(label for label, _ in LOOKBACK_POINTS)}")
    print(f"       Threshold : {LOSER_THRESHOLD_PCT}% at ANY point\n")

    results = []
    done = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures_map = {executor.submit(check_symbol_losses_old, sym): sym for sym in symbols}
        for future in as_completed(futures_map):
            done += 1
            try:
                res = future.result()
                if res and res["qualified"]:
                    results.append(res)
            except Exception:
                pass
            if done % 30 == 0 or done == len(symbols):
                print(f"       Progress: {done}/{len(symbols)} | Losers found: {len(results)}")

    print(f"\n       {len(results)} tokens qualified as historical losers (OLD method)\n")
    return results
