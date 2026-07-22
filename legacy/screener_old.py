"""
Legacy screener orchestration logic.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import pandas as pd

from legacy.config_old import (
    CVD_INTERVALS,
    CVD_LOOKBACK_HOURS,
    FINAL_TOP_N,
    LOOKBACK_POINTS,
    LOSER_MODE,
    LOSER_THRESHOLD_PCT,
    MAX_WORKERS,
    MIN_LOSER_TIMEFRAMES,
    MIN_VOLUME_USDT,
)
from legacy.cvd_old import scan_cvd, scan_cvd_std
from legacy.data_fetcher_old import get_symbols
from legacy.filters_old import find_historical_losers, find_historical_losers_old


RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"


def run_screener():
    print("=" * 70)
    print("  SHORT SQUEEZE SCREENER (LEGACY COPY)")

    if LOSER_MODE == "MIN":
        mode_str = f"at least {MIN_LOSER_TIMEFRAMES}"
    else:
        mode_str = LOSER_MODE

    print(f"  Loser filter : {LOSER_THRESHOLD_PCT}% or worse ({mode_str} timeframes)")
    print(f"  Timeframes   : {', '.join(label for label, _ in LOOKBACK_POINTS)}")
    print(f"  CVD window   : {CVD_LOOKBACK_HOURS} candles")
    print(f"  CVD Intervals: {', '.join(CVD_INTERVALS)}")
    print(f"  Min volume   : ${MIN_VOLUME_USDT:,} / day")
    print("=" * 70)

    symbols = get_symbols()

    print("\n" + "=" * 70)
    print("  TABLE 1: Current Price vs Historical Points")
    print("=" * 70)

    losers = find_historical_losers(symbols)
    if not losers:
        print("No historical losers found. Try lowering LOSER_THRESHOLD_PCT.")
        return

    loser_syms = [row["symbol"] for row in losers]
    loser_map = {row["symbol"]: row for row in losers}

    print(f"[3/4] CVD divergence scan on {len(loser_syms)} loser tokens...")
    cvd_results = []
    done = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures_map = {executor.submit(scan_cvd, sym): sym for sym in loser_syms}
        for future in as_completed(futures_map):
            sym = futures_map[future]
            done += 1
            try:
                cvd = future.result()
                if cvd:
                    cvd_results.append({**cvd, **loser_map[sym]})
            except Exception:
                pass
            if done % 10 == 0 or done == len(loser_syms):
                signals = sum(1 for row in cvd_results if row.get("signal"))
                print(f"      Progress: {done}/{len(loser_syms)} | Any Signal: {signals}")

    if not cvd_results:
        print("No CVD data returned. Check your internet connection.")
        return

    print("\n[4/4] Ranking results...\n")
    df1 = pd.DataFrame(cvd_results)
    if df1.empty:
        print("No valid results to rank.")
        return

    if "signal" not in df1.columns:
        df1["signal"] = False

    rank_col = "score_1h" if "score_1h" in df1.columns else None
    if not rank_col:
        score_cols = [col for col in df1.columns if col.startswith("score_")]
        rank_col = score_cols[0] if score_cols else None

    df1["_sig"] = df1["signal"].astype(int)
    if rank_col:
        df1 = df1.sort_values(["_sig", rank_col], ascending=[False, False])
    else:
        df1 = df1.sort_values(["_sig"], ascending=[False])

    cols_to_drop = ["_sig"]
    if "qualified" in df1.columns:
        cols_to_drop.append("qualified")
    df1 = df1.drop(columns=cols_to_drop).reset_index(drop=True)

    tf_labels = [label for label, _ in LOOKBACK_POINTS]
    top = df1.head(FINAL_TOP_N)
    table_width = 50 + (len(tf_labels) * 11) + (len(CVD_INTERVALS) * 6)

    print("=" * table_width)
    print(f"  TOP {FINAL_TOP_N} SHORT SQUEEZE CANDIDATES (LEGACY)")
    print("  Method: Current Price vs Historical Points")
    print(f"  Scanned: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * table_width)

    header = f"{'#':<4} {'Symbol':<12}"
    for interval in CVD_INTERVALS:
        header += f" {interval:>5}"
    for tf in tf_labels:
        header += f"  {tf:>9}"
    print(header)
    print("-" * table_width)

    for idx, row in top.iterrows():
        line = f"{idx + 1:<4} {row['symbol']:<12}"
        for interval in CVD_INTERVALS:
            line += f" {('Y' if row.get(f'sig_{interval}', False) else '-'):>5}"
        for tf in tf_labels:
            value = row.get(tf)
            line += f"  {f'{value:+.1f}%' if value is not None else 'N/A':>9}"
        print(line)

    RESULTS_DIR.mkdir(exist_ok=True)
    path1 = RESULTS_DIR / f"squeeze_current_legacy_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    df1.to_csv(path1, index=False)
    fname1 = path1.name
    print(f"\n  Full results saved -> {fname1}\n")

    print("\n" + "=" * 70)
    print("  TABLE 2: 24h Candle Change at Historical Points (OLD METHOD)")
    print("=" * 70)

    losers_old = find_historical_losers_old(symbols)
    if not losers_old:
        print("No historical losers found with old method.")
        return

    loser_syms_old = [row["symbol"] for row in losers_old]
    loser_map_old = {row["symbol"]: row for row in losers_old}

    print(f"[3b/4] CVD divergence scan on {len(loser_syms_old)} loser tokens (OLD method)...")
    cvd_results_old = []
    done = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures_map = {executor.submit(scan_cvd, sym): sym for sym in loser_syms_old}
        for future in as_completed(futures_map):
            sym = futures_map[future]
            done += 1
            try:
                cvd = future.result()
                if cvd:
                    cvd_results_old.append({**cvd, **loser_map_old[sym]})
            except Exception:
                pass
            if done % 10 == 0 or done == len(loser_syms_old):
                signals = sum(1 for row in cvd_results_old if row.get("signal"))
                print(f"       Progress: {done}/{len(loser_syms_old)} | Any Signal: {signals}")

    if not cvd_results_old:
        print("No CVD data returned for old method.")
        return

    print("\n[4b/4] Ranking old method results...\n")
    df2 = pd.DataFrame(cvd_results_old)
    if df2.empty:
        print("No valid results to rank for old method.")
        return

    if "signal" not in df2.columns:
        df2["signal"] = False

    rank_col = "score_1h" if "score_1h" in df2.columns else None
    if not rank_col:
        score_cols = [col for col in df2.columns if col.startswith("score_")]
        rank_col = score_cols[0] if score_cols else None

    df2["_sig"] = df2["signal"].astype(int)
    if rank_col:
        df2 = df2.sort_values(["_sig", rank_col], ascending=[False, False])
    else:
        df2 = df2.sort_values(["_sig"], ascending=[False])

    cols_to_drop = ["_sig"]
    if "qualified" in df2.columns:
        cols_to_drop.append("qualified")
    df2 = df2.drop(columns=cols_to_drop).reset_index(drop=True)

    top2 = df2.head(FINAL_TOP_N)
    print("=" * table_width)
    print(f"  TOP {FINAL_TOP_N} SHORT SQUEEZE CANDIDATES (OLD METHOD)")
    print("  Method: 24h Candle Change at Historical Points")
    print(f"  Scanned: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * table_width)
    print(header)
    print("-" * table_width)

    for idx, row in top2.iterrows():
        line = f"{idx + 1:<4} {row['symbol']:<12}"
        for interval in CVD_INTERVALS:
            line += f" {('Y' if row.get(f'sig_{interval}', False) else '-'):>5}"
        for tf in tf_labels:
            value = row.get(tf)
            line += f"  {f'{value:+.1f}%' if value is not None else 'N/A':>9}"
        print(line)

    path2 = RESULTS_DIR / f"squeeze_old_legacy_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    df2.to_csv(path2, index=False)
    fname2 = path2.name
    print(f"\n  Full results saved -> {fname2}\n")

    print("\n" + "=" * 70)
    print("  TABLE 3: Clean Divergences (Z-Score Standardized)")
    print("=" * 70)

    print(f"[3c/4] Standardized CVD scan on {len(loser_syms)} loser tokens...")
    cvd_results_std = []
    done = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures_map = {executor.submit(scan_cvd_std, sym): sym for sym in loser_syms}
        for future in as_completed(futures_map):
            sym = futures_map[future]
            done += 1
            try:
                cvd = future.result()
                if cvd:
                    cvd_results_std.append({**cvd, **loser_map[sym]})
            except Exception:
                pass
            if done % 10 == 0 or done == len(loser_syms):
                clean_signals = sum(1 for row in cvd_results_std if row.get("signal"))
                print(f"       Progress: {done}/{len(loser_syms)} | Clean Signals: {clean_signals}")

    if cvd_results_std:
        df3 = pd.DataFrame(cvd_results_std)
        df3_clean = df3[df3["signal"] == True].copy()
        if not df3_clean.empty:
            df3_clean = df3_clean.sort_values("clean_score", ascending=False).reset_index(drop=True)
            path3 = RESULTS_DIR / f"squeeze_clean_legacy_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
            df3_clean.to_csv(path3, index=False)
            fname3 = path3.name
            print(f"\n  Full clean divergence results saved -> {fname3}\n")

    print("=" * 95)
    print("  Legacy copy finished.")
    print(f"  Current method results -> {fname1}")
    print(f"  Old method results     -> {fname2}")
    print("=" * 95 + "\n")
