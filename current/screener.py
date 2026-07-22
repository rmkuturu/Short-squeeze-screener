"""
Main screener orchestration logic.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from current.config import (
    CVD_INTERVALS,
    CVD_LOOKBACK_BY_INTERVAL,
    CVD_STRONG_ZSCORE,
    FINAL_TOP_N,
    LOOKBACK_POINTS,
    LOSER_MODE,
    LOSER_THRESHOLD_PCT,
    MAX_WORKERS,
    MIN_LOSER_TIMEFRAMES,
    MIN_VOLUME_USDT,
    OPEN_INTEREST_PERIOD,
    RANK_WEIGHT_CLEAN_DIVERGENCE,
    RANK_WEIGHT_LOSS_COUNT,
    RANK_WEIGHT_LOSS_SEVERITY,
    RANK_WEIGHT_OPEN_INTEREST,
    RANK_WEIGHT_RAW_DIVERGENCE,
    RANK_WEIGHT_RELATIVE_VOLUME,
    RANK_WEIGHT_SIGNAL_COUNT,
    RANK_WEIGHT_STRONG_COUNT,
)
from current.cvd import scan_cvd, scan_cvd_std
from current.data_fetcher import get_open_interest_metrics, get_relative_volume, get_symbols
from current.filters import find_historical_losers, find_historical_losers_old


RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"


def _mode_label() -> str:
    if LOSER_MODE == "MIN":
        return f"at least {MIN_LOSER_TIMEFRAMES}"
    return LOSER_MODE


def _safe_char(flag: bool) -> str:
    return "Y" if flag else "-"


def _scan_label(scope: str) -> str:
    return "[3/4] Full-market CVD divergence scan" if scope == "full_market" else "[3/4] CVD divergence scan"


def _output_columns(df: pd.DataFrame) -> list[str]:
    base_cols = [
        "symbol",
        "composite_score",
        "signal_count",
        "strong_signal_count",
        "clean_signal_count",
        "best_interval",
        "best_interval_score",
        "raw_divergence_score",
        "clean_score",
        "relative_volume",
        "relative_open_interest",
        "open_interest_change_pct",
        "worst_loss",
        "avg_return",
        "qualified_count",
    ]
    interval_cols = []
    for interval in CVD_INTERVALS:
        interval_cols.extend([
            f"sig_{interval}",
            f"strong_{interval}",
            f"clean_{interval}",
        ])
    timeframe_cols = [label for label, _ in LOOKBACK_POINTS]
    ordered = base_cols + interval_cols + timeframe_cols
    return [col for col in ordered if col in df.columns]


def _apply_interval_filter(df: pd.DataFrame, intervals: list[str], clean_only: bool) -> pd.DataFrame:
    if df.empty or not intervals:
        return df

    flag_prefix = "clean" if clean_only else "sig"
    flag_cols = [f"{flag_prefix}_{interval}" for interval in intervals if f"{flag_prefix}_{interval}" in df.columns]
    if not flag_cols:
        return df

    mask = df[flag_cols].fillna(False).any(axis=1)
    return df[mask].copy()


def build_scan_results(
    scope: str = "loser_only",
    signal_mode: str = "clean",
    intervals: Optional[list[str]] = None,
) -> dict:
    """
    Run an in-memory scan for the local dashboard.
    scope: 'loser_only' or 'full_market'
    signal_mode: 'clean' or 'raw'
    intervals: optional interval filter like ['12h', '1d']
    """
    symbols = get_symbols()

    if scope == "full_market":
        scan_symbols = symbols
        loser_map = {}
        losers = []
    else:
        losers = find_historical_losers(symbols)
        scan_symbols = [row["symbol"] for row in losers]
        loser_map = {row["symbol"]: row for row in losers}

    scanner = scan_cvd_std if signal_mode == "clean" else scan_cvd
    clean_only = signal_mode == "clean"
    rows = _scan_candidates(scan_symbols, loser_map, scanner, _scan_label(scope))
    ranked = _prepare_ranked_frame(rows, clean_only=clean_only)
    ranked = _apply_interval_filter(ranked, intervals or [], clean_only=clean_only)

    display = ranked[_output_columns(ranked)].copy() if not ranked.empty else ranked.copy()
    return {
        "symbols_scanned": len(symbols),
        "losers_found": len(losers),
        "candidates_scanned": len(scan_symbols),
        "result_count": len(ranked),
        "clean_only": clean_only,
        "scope": scope,
        "signal_mode": signal_mode,
        "intervals": intervals or [],
        "ranked": ranked,
        "display": display,
    }


def _scan_candidates(symbols: list[str], loser_map: dict, scanner, label: str) -> list[dict]:
    print(f"{label} on {len(symbols)} loser tokens...")
    results = []
    done = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures_map = {executor.submit(scanner, sym): sym for sym in symbols}
        for future in as_completed(futures_map):
            sym = futures_map[future]
            done += 1
            try:
                cvd = future.result()
                if cvd:
                    results.append({
                        **cvd,
                        **get_relative_volume(sym),
                        **get_open_interest_metrics(sym),
                        **loser_map.get(sym, {"symbol": sym}),
                    })
            except Exception as exc:
                print(f"      [warn] CVD scan failed for {sym}: {exc}")
            if done % 10 == 0 or done == len(symbols):
                signals = sum(1 for row in results if row.get("signal"))
                print(f"      Progress: {done}/{len(symbols)} | Candidates with signal: {signals}")

    return results


def _prepare_ranked_frame(rows: list[dict], clean_only: bool = False) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return df

    defaults = {
        "signal": False,
        "signal_count": 0,
        "strong_signal_count": 0,
        "clean_signal_count": 0,
        "raw_divergence_score": 0.0,
        "clean_score": 0.0,
        "qualified_count": 0,
        "worst_loss": 0.0,
        "avg_return": 0.0,
        "best_interval": "",
        "best_interval_score": 0.0,
        "relative_volume": 0.0,
        "current_quote_volume": 0.0,
        "avg_quote_volume": 0.0,
        "relative_open_interest": 0.0,
        "open_interest_change_pct": 0.0,
        "open_interest_value": 0.0,
        "avg_open_interest_value": 0.0,
    }
    for col, default in defaults.items():
        if col not in df.columns:
            df[col] = default

    if clean_only:
        df = df[df["clean_signal_count"] > 0].copy()
        if df.empty:
            return df

    df["loss_severity_score"] = df["worst_loss"].fillna(0.0).abs()
    if clean_only:
        df["composite_score"] = (
            df["clean_signal_count"] * (RANK_WEIGHT_STRONG_COUNT + 8.0)
            + df["clean_score"] * (RANK_WEIGHT_CLEAN_DIVERGENCE + 6.0)
            + df["strong_signal_count"] * 6.0
            + df["raw_divergence_score"] * 8.0
            + df["relative_volume"] * (RANK_WEIGHT_RELATIVE_VOLUME + 4.0)
            + df["relative_open_interest"] * (RANK_WEIGHT_OPEN_INTEREST + 2.0)
            + df["open_interest_change_pct"].clip(lower=0.0) * 0.8
            + df["loss_severity_score"] * 0.35
            + df["qualified_count"] * 2.0
        ).round(4)
        sort_cols = ["clean_signal_count", "clean_score", "relative_volume", "relative_open_interest", "strong_signal_count", "composite_score", "best_interval_score"]
        ascending = [False, False, False, False, False, False, False]
    else:
        df["composite_score"] = (
            df["signal_count"] * RANK_WEIGHT_SIGNAL_COUNT
            + df["strong_signal_count"] * RANK_WEIGHT_STRONG_COUNT
            + df["raw_divergence_score"] * RANK_WEIGHT_RAW_DIVERGENCE
            + df["clean_score"] * RANK_WEIGHT_CLEAN_DIVERGENCE
            + df["relative_volume"] * RANK_WEIGHT_RELATIVE_VOLUME
            + df["relative_open_interest"] * RANK_WEIGHT_OPEN_INTEREST
            + df["open_interest_change_pct"].clip(lower=0.0) * 0.6
            + df["loss_severity_score"] * RANK_WEIGHT_LOSS_SEVERITY
            + df["qualified_count"] * RANK_WEIGHT_LOSS_COUNT
        ).round(4)
        sort_cols = ["signal", "strong_signal_count", "relative_volume", "relative_open_interest", "composite_score", "clean_score", "best_interval_score"]
        ascending = [False, False, False, False, False, False, False]

    df = df.sort_values(sort_cols, ascending=ascending).reset_index(drop=True)
    return df


def _print_ranked_table(title: str, subtitle: str, df: pd.DataFrame, clean_only: bool = False) -> None:
    tf_labels = [label for label, _ in LOOKBACK_POINTS]
    top = df.head(FINAL_TOP_N)
    table_width = 95 + (len(CVD_INTERVALS) * 6) + (len(tf_labels) * 11)

    print("=" * table_width)
    print(f"  {title}")
    print(f"  {subtitle}")
    print(f"  Scanned: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * table_width)

    count_label = "Clean" if clean_only else "Strong"
    header = f"{'#':<4} {'Symbol':<12} {'Score':>8} {'RVol':>6} {'Sig':>4} {count_label:>6} {'Best':>6}"
    for interval in CVD_INTERVALS:
        header += f" {interval:>5}"
    for tf in tf_labels:
        header += f"  {tf:>9}"
    print(header)
    print("-" * table_width)

    for idx, row in top.iterrows():
        line = (
            f"{idx + 1:<4} {row['symbol']:<12} "
            f"{row['composite_score']:>8.2f} "
            f"{row.get('relative_volume', 0.0):>6.2f} "
            f"{int(row['signal_count']):>4} "
            f"{int(row['clean_signal_count'] if clean_only else row['strong_signal_count']):>6} "
            f"{str(row.get('best_interval') or '-'):>6}"
        )
        for interval in CVD_INTERVALS:
            interval_flag = row.get(f"clean_{interval}", False) if clean_only else row.get(f"sig_{interval}", False)
            line += f" {_safe_char(interval_flag):>5}"
        for tf in tf_labels:
            value = row.get(tf)
            line += f"  {f'{value:+.1f}%' if pd.notna(value) else 'N/A':>9}"
        print(line)

    print("=" * table_width)
    print("  RVol   = current 24h futures quote volume / average completed daily quote volume")
    print("  Sig    = count of intervals with spot CVD up and futures CVD down")
    if clean_only:
        print(f"  Clean  = count of intervals where raw divergence also passes z-score >= {CVD_STRONG_ZSCORE}")
        print("  Y/-    = interval-level clean divergence flag")
    else:
        print(f"  Strong = count of intervals with z-score strength >= {CVD_STRONG_ZSCORE}")
        print("  Y/-    = interval-level raw divergence flag")
    print("=" * table_width + "\n")


def _save_results(df: pd.DataFrame, prefix: str) -> str:
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    df[_output_columns(df)].to_csv(path, index=False)
    print(f"  Full results saved -> {path.name}\n")
    return path.name


def _run_method(symbols: list[str], title: str, subtitle: str, loser_finder, scanner, output_prefix: str, clean_only: bool = False):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)

    losers = loser_finder(symbols)
    if not losers:
        print("No historical losers found for this method.")
        return None

    loser_syms = [row["symbol"] for row in losers]
    loser_map = {row["symbol"]: row for row in losers}
    rows = _scan_candidates(loser_syms, loser_map, scanner, "[3/4] CVD divergence scan")
    if not rows:
        print("No CVD data returned. Check your internet connection.")
        return None

    print("[4/4] Ranking results...\n")
    ranked = _prepare_ranked_frame(rows, clean_only=clean_only)
    if ranked.empty:
        print("No ranked candidates for this method.")
        return None

    _print_ranked_table(
        title=f"TOP {min(len(ranked), FINAL_TOP_N)} SHORT SQUEEZE CANDIDATES",
        subtitle=subtitle,
        df=ranked,
        clean_only=clean_only,
    )
    return _save_results(ranked, output_prefix)


def _run_market_method(symbols: list[str], scanner, output_prefix: str, clean_only: bool = False):
    print("\n" + "=" * 70)
    print("  TABLE 4: Full Market CVD Divergence")
    print("=" * 70)

    rows = _scan_candidates(symbols, {}, scanner, "[3/4] Full-market CVD divergence scan")
    if not rows:
        print("No CVD data returned. Check your internet connection.")
        return None

    print("[4/4] Ranking full-market results...\n")
    ranked = _prepare_ranked_frame(rows, clean_only=clean_only)
    if ranked.empty:
        print("No ranked candidates for the full-market method.")
        return None

    _print_ranked_table(
        title=f"TOP {min(len(ranked), FINAL_TOP_N)} FULL-MARKET CVD CANDIDATES",
        subtitle="Method: all liquid spot+futures symbols, no historical-loser filter",
        df=ranked,
        clean_only=clean_only,
    )
    return _save_results(ranked, output_prefix)


def run_screener():
    cvd_window_str = ", ".join(f"{interval}={CVD_LOOKBACK_BY_INTERVAL.get(interval)} candles" for interval in CVD_INTERVALS)

    print("=" * 70)
    print("  SHORT SQUEEZE SCREENER (V2)")
    print(f"  Loser filter : {LOSER_THRESHOLD_PCT}% or worse ({_mode_label()} timeframes)")
    print(f"  Timeframes   : {', '.join(label for label, _ in LOOKBACK_POINTS)}")
    print(f"  CVD window   : {cvd_window_str}")
    print(f"  CVD Intervals: {', '.join(CVD_INTERVALS)}")
    print(f"  Min volume   : ${MIN_VOLUME_USDT:,} / day")
    print("=" * 70)

    symbols = get_symbols()

    fname1 = _run_method(
        symbols=symbols,
        title="TABLE 1: Current Price vs Historical Points",
        subtitle="Method: Current price compared with historical checkpoints",
        loser_finder=find_historical_losers,
        scanner=scan_cvd,
        output_prefix="squeeze_current",
    )

    fname2 = _run_method(
        symbols=symbols,
        title="TABLE 2: Historical 24h Candle Change",
        subtitle="Method: 24h daily candle return at historical checkpoints",
        loser_finder=find_historical_losers_old,
        scanner=scan_cvd,
        output_prefix="squeeze_old",
    )

    fname3 = _run_method(
        symbols=symbols,
        title="TABLE 3: Clean Divergences",
        subtitle="Method: only statistically strong spot/futures divergences",
        loser_finder=find_historical_losers,
        scanner=scan_cvd_std,
        output_prefix="squeeze_clean",
        clean_only=True,
    )

    fname4 = _run_market_method(
        symbols=symbols,
        scanner=scan_cvd,
        output_prefix="squeeze_market",
    )

    print("=" * 95)
    print("  SQUEEZE = spot buying pressure rising while futures pressure leans the other way")
    print("  Use the ranked shortlist as a watchlist, then confirm on chart before taking risk.")
    if fname1:
        print(f"  Table 1 -> {fname1}")
    if fname2:
        print(f"  Table 2 -> {fname2}")
    if fname3:
        print(f"  Table 3 -> {fname3}")
    if fname4:
        print(f"  Table 4 -> {fname4}")
    print("=" * 95 + "\n")
