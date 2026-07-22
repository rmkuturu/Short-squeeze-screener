"""
Historical loser filter logic.
"""

from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from current.config import LOSER_THRESHOLD_PCT, LOOKBACK_POINTS, MAX_WORKERS, LOSER_MODE, MIN_LOSER_TIMEFRAMES
from current.data_fetcher import get_pct_change_at, get_candle_change_at


def check_symbol_losses(sym: str) -> Optional[dict]:
    """Check one symbol across all lookback timeframes (current price method)."""
    row = {"symbol": sym, "qualified": False}
    loss_values = []
    meets_threshold_count = 0
    valid_timeframes = 0
    
    for label, days_ago in LOOKBACK_POINTS:
        pct = get_pct_change_at(sym, days_ago)
        row[label] = pct
        if pct is not None:
            valid_timeframes += 1
            loss_values.append(pct)
            if pct <= LOSER_THRESHOLD_PCT:
                meets_threshold_count += 1
    
    if LOSER_MODE == 'ALL':
        row["qualified"] = (meets_threshold_count == valid_timeframes) and (valid_timeframes > 0)
    elif LOSER_MODE == 'MIN':
        row["qualified"] = (meets_threshold_count >= MIN_LOSER_TIMEFRAMES)
    else:
        # Default 'ANY' logic
        row["qualified"] = (meets_threshold_count > 0)
        
    row["qualified_count"] = meets_threshold_count
    row["valid_timeframes"] = valid_timeframes
    row["worst_loss"] = min(loss_values) if loss_values else None
    row["avg_return"] = round(sum(loss_values) / len(loss_values), 2) if loss_values else None
    return row


def check_symbol_losses_old(sym: str) -> Optional[dict]:
    """Check one symbol using OLD method: 24h candle change at historical points."""
    row = {"symbol": sym, "qualified": False}
    loss_values = []
    meets_threshold_count = 0
    valid_timeframes = 0
    
    for label, days_ago in LOOKBACK_POINTS:
        # OLD method: get 24h change of the daily candle that closed X days ago
        pct = get_candle_change_at(sym, int(days_ago))
        row[label] = pct
        if pct is not None:
            valid_timeframes += 1
            loss_values.append(pct)
            if pct <= LOSER_THRESHOLD_PCT:
                meets_threshold_count += 1
    
    if LOSER_MODE == 'ALL':
        row["qualified"] = (meets_threshold_count == valid_timeframes) and (valid_timeframes > 0)
    elif LOSER_MODE == 'MIN':
        row["qualified"] = (meets_threshold_count >= MIN_LOSER_TIMEFRAMES)
    else:
        row["qualified"] = (meets_threshold_count > 0)
        
    row["qualified_count"] = meets_threshold_count
    row["valid_timeframes"] = valid_timeframes
    row["worst_loss"] = min(loss_values) if loss_values else None
    row["avg_return"] = round(sum(loss_values) / len(loss_values), 2) if loss_values else None
    return row


def find_historical_losers(symbols: list) -> list:
    """
    Scans all symbols for historical losses (current price method).
    Qualifies if -10% or worse at ANY past reference point.
    """
    print(f"[2/4] Scanning historical % changes ({len(symbols)} tokens)...")
    print(f"      Timeframes: {', '.join(l for l, _ in LOOKBACK_POINTS)}")
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
            except Exception as exc:
                print(f"      [warn] loser scan failed for {futures_map[future]}: {exc}")
            if done % 30 == 0 or done == len(symbols):
                print(f"      Progress: {done}/{len(symbols)} | Losers found: {len(results)}")

    print(f"\n      {len(results)} tokens qualified as historical losers\n")
    return results


def find_historical_losers_old(symbols: list) -> list:
    """
    OLD METHOD: Scans all symbols for 24h candle changes at historical points.
    """
    print(f"[2b/4] Scanning OLD method - 24h candle changes ({len(symbols)} tokens)...")
    print(f"       Timeframes: {', '.join(l for l, _ in LOOKBACK_POINTS)}")
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
            except Exception as exc:
                print(f"       [warn] old loser scan failed for {futures_map[future]}: {exc}")
            if done % 30 == 0 or done == len(symbols):
                print(f"       Progress: {done}/{len(symbols)} | Losers found: {len(results)}")

    print(f"\n       {len(results)} tokens qualified as historical losers (OLD method)\n")
    return results
