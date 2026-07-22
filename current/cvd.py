"""
CVD (Cumulative Volume Delta) divergence analysis.
"""

import numpy as np
from scipy.stats import linregress
from typing import Optional
from current.config import (
    CVD_INTERVALS,
    CVD_LOOKBACK_BY_INTERVAL,
    CVD_STRONG_ZSCORE,
    CVD_ZSCORE_LOOKBACK,
)
from current.data_fetcher import fetch_cvd_candles

def compute_cvd_slope(candles: list) -> Optional[float]:
    """
    CVD delta per candle = 2 * taker_buy_vol - total_vol
    (buyers = taker_buy, sellers = total - taker_buy)
    Returns slope normalized by avg volume -> comparable across market caps.
    """
    if not candles or len(candles) < 2:
        return None

    deltas = [2 * tbv - tv for tbv, tv in candles]
    cvd = np.cumsum(deltas)
    slope, *_ = linregress(np.arange(len(cvd)), cvd)

    avg_vol = np.mean([tv for _, tv in candles])
    return slope / avg_vol if avg_vol > 0 else slope


def _windowed_candles(candles: list, interval: str) -> list:
    window = CVD_LOOKBACK_BY_INTERVAL.get(interval, 4)
    return candles[-window:] if len(candles) > window else candles


def compute_cvd_slope_std(candles: list, lookback: int = 20) -> Optional[tuple]:
    """
    Computes CVD slope AND z-score vs recent history.
    Returns (slope, z_score) where z_score = (slope - mean) / std
    """
    if not candles or len(candles) < lookback + 5:
        return None, None
    
    # Calculate rolling CVD slopes over lookback windows
    deltas = [2 * tbv - tv for tbv, tv in candles]
    cvd = np.cumsum(deltas)
    
    # Calculate slopes for each window
    slopes = []
    for i in range(lookback, len(cvd)):
        window = cvd[i-lookback:i]
        s, *_ = linregress(np.arange(len(window)), window)
        slopes.append(s)
    
    if len(slopes) < 3:
        return None, None
    
    # Current slope (last window)
    current_slope = slopes[-1]
    
    # Z-score vs historical slopes
    mean_slope = np.mean(slopes[:-1])  # Exclude current
    std_slope = np.std(slopes[:-1])
    
    if std_slope == 0:
        z_score = 0
    else:
        z_score = (current_slope - mean_slope) / std_slope
    
    avg_vol = np.mean([tv for _, tv in candles])
    norm_slope = current_slope / avg_vol if avg_vol > 0 else current_slope
    
    return norm_slope, z_score


def _empty_interval_results(results: dict, interval: str) -> None:
    results[f"sig_{interval}"] = False
    results[f"strong_{interval}"] = False
    results[f"score_{interval}"] = 0.0
    results[f"clean_score_{interval}"] = 0.0
    results[f"z_spot_{interval}"] = 0.0
    results[f"z_fut_{interval}"] = 0.0
    results[f"clean_{interval}"] = False


def _finalize_results(results: dict, prefer_clean: bool = False) -> dict:
    interval_scores = {interval: results.get(f"score_{interval}", 0.0) for interval in CVD_INTERVALS}
    clean_interval_scores = {interval: results.get(f"clean_score_{interval}", 0.0) for interval in CVD_INTERVALS}
    chosen_scores = clean_interval_scores if prefer_clean else interval_scores
    best_interval = max(chosen_scores, key=chosen_scores.get) if chosen_scores else None

    results["signal_count"] = sum(1 for interval in CVD_INTERVALS if results.get(f"sig_{interval}", False))
    results["strong_signal_count"] = sum(1 for interval in CVD_INTERVALS if results.get(f"strong_{interval}", False))
    results["clean_signal_count"] = sum(1 for interval in CVD_INTERVALS if results.get(f"clean_{interval}", False))
    results["raw_divergence_score"] = round(sum(max(results.get(f"score_{i}", 0.0), 0.0) for i in CVD_INTERVALS), 6)
    results["clean_score"] = round(sum(results.get(f"z_spot_{i}", 0.0) * abs(results.get(f"z_fut_{i}", 0.0))
                                       for i in CVD_INTERVALS if results.get(f"clean_{i}", False)), 4)
    results["best_interval"] = best_interval
    results["best_interval_score"] = round(chosen_scores.get(best_interval, 0.0), 6) if best_interval else 0.0
    results["signal"] = results["signal_count"] > 0
    return results


def scan_cvd(symbol: str) -> Optional[dict]:
    """Runs CVD divergence check on a single symbol across all configured intervals."""
    results = {"symbol": symbol}
    
    for interval in CVD_INTERVALS:
        signal_window = CVD_LOOKBACK_BY_INTERVAL.get(interval, 4)
        candle_limit = max(signal_window, CVD_ZSCORE_LOOKBACK + signal_window + 2)
        spot_c = fetch_cvd_candles(symbol, "spot", candle_limit, interval)
        futures_c = fetch_cvd_candles(symbol, "futures", candle_limit, interval)

        if not spot_c or not futures_c:
            _empty_interval_results(results, interval)
            continue

        s_slope = compute_cvd_slope(_windowed_candles(spot_c, interval))
        f_slope = compute_cvd_slope(_windowed_candles(futures_c, interval))
        _, s_z = compute_cvd_slope_std(spot_c, CVD_ZSCORE_LOOKBACK)
        _, f_z = compute_cvd_slope_std(futures_c, CVD_ZSCORE_LOOKBACK)

        if s_slope is None or f_slope is None:
            _empty_interval_results(results, interval)
            continue

        score = round(s_slope - f_slope, 6)
        signal = s_slope > 0 and f_slope < 0
        strong_signal = signal and (s_z is not None and s_z >= CVD_STRONG_ZSCORE) and (f_z is not None and f_z <= -CVD_STRONG_ZSCORE)
        
        results[f"sig_{interval}"] = signal
        results[f"strong_{interval}"] = strong_signal
        results[f"score_{interval}"] = score
        results[f"z_spot_{interval}"] = round(s_z, 2) if s_z is not None else 0.0
        results[f"z_fut_{interval}"] = round(f_z, 2) if f_z is not None else 0.0
        results[f"clean_score_{interval}"] = round(results[f"z_spot_{interval}"] * abs(results[f"z_fut_{interval}"]), 4) if strong_signal else 0.0
        results[f"clean_{interval}"] = strong_signal

    return _finalize_results(results)


def scan_cvd_std(symbol: str) -> Optional[dict]:
    """
    STANDARDIZED CVD scan with z-scores.
    Returns clean divergence score based on statistical significance.
    """
    results = {"symbol": symbol}
    
    for interval in CVD_INTERVALS:
        signal_window = CVD_LOOKBACK_BY_INTERVAL.get(interval, 4)
        candle_limit = max(signal_window, CVD_ZSCORE_LOOKBACK + signal_window + 2)
        spot_c = fetch_cvd_candles(symbol, "spot", candle_limit, interval)
        futures_c = fetch_cvd_candles(symbol, "futures", candle_limit, interval)

        if not spot_c or not futures_c:
            _empty_interval_results(results, interval)
            continue

        # Get slopes and z-scores
        s_slope, s_z = compute_cvd_slope_std(spot_c)
        f_slope, f_z = compute_cvd_slope_std(futures_c)

        if s_slope is None or f_slope is None:
            _empty_interval_results(results, interval)
            continue

        signal = (s_slope > 0 and f_slope < 0)
        clean_div = signal and (s_z >= CVD_STRONG_ZSCORE) and (f_z <= -CVD_STRONG_ZSCORE)

        results[f"sig_{interval}"] = signal
        results[f"strong_{interval}"] = clean_div
        results[f"score_{interval}"] = round(s_slope - f_slope, 6)
        results[f"clean_score_{interval}"] = round(s_z * abs(f_z), 4) if clean_div else 0.0
        results[f"z_spot_{interval}"] = round(s_z, 2) if s_z is not None else 0.0
        results[f"z_fut_{interval}"] = round(f_z, 2) if f_z is not None else 0.0
        results[f"clean_{interval}"] = clean_div

    return _finalize_results(results, prefer_clean=True)
