"""
Short Squeeze Screener — Final Version
=======================================
Filter 1: Top losers at ANY of these past points:
           24h ago, 3d ago, 7d ago, 30d ago, 60d ago (-10% or worse)

Filter 2: CVD divergence on 1h timeframe:
           Spot CVD trending UP + Futures CVD trending DOWN

Output: Ranked shortlist of ~20 squeeze candidates

Install:
  pip install requests pandas numpy scipy

Run:
  python squeeze_screener.py
"""

import time
import requests
import pandas as pd
import numpy as np
from scipy.stats import linregress
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

# ─── CONFIG ───────────────────────────────────────────────────────────────────

LOSER_THRESHOLD_PCT = -10.0     # % drop to qualify as a loser
MIN_VOLUME_USDT     = 1_000_000 # Min 24h futures volume (filters micro-caps)
CVD_LOOKBACK_HOURS  = 6         # Number of 1h candles for CVD slope
FINAL_TOP_N         = 20        # Final shortlist size
MAX_WORKERS         = 8         # Parallel threads

# Past reference points: (label, days_ago)
# Qualifies if -10% or worse at ANY single point
LOOKBACK_POINTS = [
    ("24h_ago",  1),
    ("3d_ago",   3),
    ("7d_ago",   7),
    ("30d_ago",  30),
    ("60d_ago",  60),
]

# ─── UTILS ────────────────────────────────────────────────────────────────────

def ts_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

# ─── STEP 1: GET SYMBOLS ──────────────────────────────────────────────────────

def get_symbols() -> list:
    """
    Returns symbols available on BOTH Binance USDT-M futures AND spot,
    filtered by minimum 24h futures volume.
    """
    print("\n[1/4] Fetching symbol lists from Binance...")

    r_fut = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=10)
    r_fut.raise_for_status()
    futures_syms = {
        s["symbol"] for s in r_fut.json()["symbols"]
        if s["quoteAsset"] == "USDT"
        and s["status"] == "TRADING"
        and s["contractType"] == "PERPETUAL"
    }

    r_spot = requests.get("https://api.binance.com/api/v3/exchangeInfo", timeout=10)
    r_spot.raise_for_status()
    spot_syms = {
        s["symbol"] for s in r_spot.json()["symbols"]
        if s["quoteAsset"] == "USDT"
        and s["status"] == "TRADING"
    }

    common = futures_syms & spot_syms
    print(f"     {len(futures_syms)} futures | {len(spot_syms)} spot | {len(common)} on both")

    # Volume filter — single bulk call, much faster than per-symbol calls
    print(f"     Filtering by 24h volume > ${MIN_VOLUME_USDT:,}...")
    r_vol = requests.get("https://fapi.binance.com/fapi/v1/ticker/24hr", timeout=10)
    r_vol.raise_for_status()
    vol_map = {item["symbol"]: float(item["quoteVolume"]) for item in r_vol.json()}

    qualified = sorted([
        sym for sym in common
        if vol_map.get(sym, 0) >= MIN_VOLUME_USDT
    ])
    print(f"     {len(qualified)} tokens pass volume filter\n")
    return qualified

# ─── STEP 2: HISTORICAL LOSER FILTER ─────────────────────────────────────────

def get_pct_change_at(symbol: str, days_ago: int) -> float | None:
    """
    Gets the 24h % price change of a DAILY candle that closed ~days_ago days back.
    Uses Binance futures daily klines.
    
    Example: days_ago=3 → what was price change on the daily candle 3 days ago?
    """
    now = now_utc()

    # Target: daily candle that CLOSES at (now - days_ago)
    # So it opens at (now - days_ago - 1 day)
    candle_close = now - timedelta(days=days_ago)
    candle_open  = candle_close - timedelta(days=1)

    params = {
        "symbol":    symbol,
        "interval":  "1d",
        "startTime": ts_ms(candle_open),
        "endTime":   ts_ms(candle_close),
        "limit":     2,
    }
    try:
        r = requests.get(
            "https://fapi.binance.com/fapi/v1/klines",
            params=params, timeout=8
        )
        r.raise_for_status()
        candles = r.json()
        if not candles:
            return None

        c = candles[0]
        open_price  = float(c[1])
        close_price = float(c[4])

        if open_price == 0:
            return None

        return round((close_price - open_price) / open_price * 100, 2)

    except Exception:
        return None

def check_symbol_losses(sym: str) -> dict | None:
    """Check one symbol across all lookback timeframes."""
    row = {"symbol": sym, "qualified": False}
    for label, days_ago in LOOKBACK_POINTS:
        pct = get_pct_change_at(sym, days_ago)
        row[label] = pct
        if pct is not None and pct <= LOSER_THRESHOLD_PCT:
            row["qualified"] = True
    return row

def find_historical_losers(symbols: list) -> list:
    """
    Scans all symbols for historical losses.
    Qualifies if -10% or worse at ANY past reference point.
    """
    print(f"[2/4] Scanning historical % changes ({len(symbols)} tokens)...")
    print(f"      Timeframes: {', '.join(l for l,_ in LOOKBACK_POINTS)}")
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

# ─── STEP 3: CVD DIVERGENCE SCAN ─────────────────────────────────────────────

def fetch_cvd_candles(symbol: str, market: str, hours: int) -> list | None:
    """
    Fetches last N completed 1h candles.
    Returns list of (taker_buy_vol, total_vol) tuples.
    Binance kline: col[5]=total vol, col[9]=taker buy base vol.
    """
    url = (
        "https://fapi.binance.com/fapi/v1/klines" if market == "futures"
        else "https://api.binance.com/api/v3/klines"
    )
    try:
        r = requests.get(
            url,
            params={"symbol": symbol, "interval": "1h", "limit": hours + 1},
            timeout=8
        )
        r.raise_for_status()
        data = r.json()[:-1]  # drop current incomplete candle
        return [(float(c[9]), float(c[5])) for c in data]
    except Exception:
        return None

def compute_cvd_slope(candles: list) -> float | None:
    """
    CVD delta per candle = 2 * taker_buy_vol - total_vol
    (buyers = taker_buy, sellers = total - taker_buy)
    Returns slope normalized by avg volume → comparable across market caps.
    """
    if not candles or len(candles) < 2:
        return None

    deltas = [2 * tbv - tv for tbv, tv in candles]
    cvd    = np.cumsum(deltas)
    slope, *_ = linregress(np.arange(len(cvd)), cvd)

    avg_vol = np.mean([tv for _, tv in candles])
    return slope / avg_vol if avg_vol > 0 else slope

def scan_cvd(symbol: str) -> dict | None:
    """Runs CVD divergence check on a single symbol."""
    spot_c    = fetch_cvd_candles(symbol, "spot",    CVD_LOOKBACK_HOURS)
    futures_c = fetch_cvd_candles(symbol, "futures", CVD_LOOKBACK_HOURS)

    if not spot_c or not futures_c:
        return None

    s_slope = compute_cvd_slope(spot_c)
    f_slope = compute_cvd_slope(futures_c)

    if s_slope is None or f_slope is None:
        return None

    return {
        "spot_slope":  round(s_slope, 6),
        "fut_slope":   round(f_slope, 6),
        "divergence":  round(s_slope - f_slope, 6),
        "signal":      s_slope > 0 and f_slope < 0,  # Classic squeeze setup
    }

# ─── STEP 4: COMBINE & DISPLAY ────────────────────────────────────────────────

def run_screener():
    print("=" * 70)
    print("  SHORT SQUEEZE SCREENER")
    print(f"  Loser filter : {LOSER_THRESHOLD_PCT}% or worse at any past point")
    print(f"  Timeframes   : {', '.join(l for l,_ in LOOKBACK_POINTS)}")
    print(f"  CVD window   : {CVD_LOOKBACK_HOURS} × 1h candles")
    print(f"  Min volume   : ${MIN_VOLUME_USDT:,} / day")
    print("=" * 70)

    # 1. Get symbols
    symbols = get_symbols()

    # 2. Historical loser filter
    losers = find_historical_losers(symbols)
    if not losers:
        print("No historical losers found. Try lowering LOSER_THRESHOLD_PCT.")
        return

    loser_syms = [r["symbol"] for r in losers]
    loser_map  = {r["symbol"]: r for r in losers}

    # 3. CVD scan on losers only
    print(f"[3/4] CVD divergence scan on {len(loser_syms)} loser tokens...")
    cvd_results = []
    done = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures_map = {executor.submit(scan_cvd, sym): sym for sym in loser_syms}
        for future in as_completed(futures_map):
            sym  = futures_map[future]
            done += 1
            try:
                cvd = future.result()
                if cvd:
                    cvd_results.append({"symbol": sym, **cvd, **loser_map[sym]})
            except Exception:
                pass
            if done % 10 == 0 or done == len(loser_syms):
                signals = sum(1 for r in cvd_results if r.get("signal"))
                print(f"      Progress: {done}/{len(loser_syms)} | Signals: {signals}")

    if not cvd_results:
        print("No CVD data returned. Check your internet connection.")
        return

    # 4. Rank: true signals first, then by divergence score
    print(f"\n[4/4] Ranking results...\n")
    df = pd.DataFrame(cvd_results)
    df["_sig"] = df["signal"].astype(int)
    df = df.sort_values(["_sig", "divergence"], ascending=[False, False])
    df = df.drop(columns=["_sig", "qualified"]).reset_index(drop=True)

    top = df.head(FINAL_TOP_N)
    tf_labels = [label for label, _ in LOOKBACK_POINTS]

    # Print table
    print("=" * 95)
    print(f"  TOP {FINAL_TOP_N} SHORT SQUEEZE CANDIDATES")
    print(f"  Scanned: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 95)

    hdr = f"{'#':<4} {'Symbol':<12} {'Spot CVD':>10} {'Fut CVD':>10} {'Score':>10}"
    for tf in tf_labels:
        hdr += f"  {tf:>9}"
    hdr += "  Signal"
    print(hdr)
    print("-" * 95)

    for i, row in top.iterrows():
        sig_str = "✅ SQUEEZE" if row["signal"] else "—"
        line = (
            f"{i+1:<4} {row['symbol']:<12} "
            f"{row['spot_slope']:>+.5f}   "
            f"{row['fut_slope']:>+.5f}   "
            f"{row['divergence']:>+.5f}"
        )
        for tf in tf_labels:
            val = row.get(tf)
            line += f"  {f'{val:+.1f}%' if val is not None else 'N/A':>9}"
        line += f"  {sig_str}"
        print(line)

    # Save CSV
    fname = f"squeeze_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    df.to_csv(fname, index=False)

    print("\n" + "=" * 95)
    print("  ✅  = Spot CVD rising + Futures CVD falling = squeeze fuel building")
    print("  Open these on your chart, confirm visually, then trade.")
    print(f"  Full results saved → {fname}")
    print("=" * 95 + "\n")

# ─── ENTRY ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    run_screener()