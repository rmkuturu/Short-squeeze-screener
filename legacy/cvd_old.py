"""
CVD divergence analysis for the legacy screener.
"""

from typing import Optional

import numpy as np
from scipy.stats import linregress

from legacy.config_old import CVD_INTERVALS, CVD_LOOKBACK_HOURS
from legacy.data_fetcher_old import fetch_cvd_candles


def compute_cvd_slope(candles: list) -> Optional[float]:
    """
    CVD delta per candle = 2 * taker_buy_vol - total_vol.
    Returns slope normalized by avg volume.
    """
    if not candles or len(candles) < 2:
        return None

    deltas = [2 * tbv - tv for tbv, tv in candles]
    cvd = np.cumsum(deltas)
    slope, *_ = linregress(np.arange(len(cvd)), cvd)

    avg_vol = np.mean([tv for _, tv in candles])
    return slope / avg_vol if avg_vol > 0 else slope


def compute_cvd_slope_std(candles: list, lookback: int = 20) -> Optional[tuple]:
    """
    Computes CVD slope and z-score vs recent history.
    """
    if not candles or len(candles) < lookback + 5:
        return None, None

    deltas = [2 * tbv - tv for tbv, tv in candles]
    cvd = np.cumsum(deltas)

    slopes = []
    for i in range(lookback, len(cvd)):
        window = cvd[i - lookback:i]
        slope, *_ = linregress(np.arange(len(window)), window)
        slopes.append(slope)

    if len(slopes) < 3:
        return None, None

    current_slope = slopes[-1]
    mean_slope = np.mean(slopes[:-1])
    std_slope = np.std(slopes[:-1])
    z_score = 0 if std_slope == 0 else (current_slope - mean_slope) / std_slope

    avg_vol = np.mean([tv for _, tv in candles])
    norm_slope = current_slope / avg_vol if avg_vol > 0 else current_slope
    return norm_slope, z_score


def scan_cvd(symbol: str) -> Optional[dict]:
    """Runs CVD divergence check on a single symbol across all configured intervals."""
    results = {"symbol": symbol}
    total_signal = False

    for interval in CVD_INTERVALS:
        spot_c = fetch_cvd_candles(symbol, "spot", CVD_LOOKBACK_HOURS, interval)
        futures_c = fetch_cvd_candles(symbol, "futures", CVD_LOOKBACK_HOURS, interval)

        if not spot_c or not futures_c:
            results[f"sig_{interval}"] = False
            results[f"score_{interval}"] = 0.0
            continue

        s_slope = compute_cvd_slope(spot_c)
        f_slope = compute_cvd_slope(futures_c)

        if s_slope is None or f_slope is None:
            results[f"sig_{interval}"] = False
            results[f"score_{interval}"] = 0.0
            continue

        score = round(s_slope - f_slope, 6)
        signal = s_slope > 0 and f_slope < 0

        results[f"sig_{interval}"] = signal
        results[f"score_{interval}"] = score

        if signal:
            total_signal = True

    results["signal"] = total_signal
    return results


def scan_cvd_std(symbol: str) -> Optional[dict]:
    """
    Standardized CVD scan with z-scores.
    """
    results = {"symbol": symbol}
    total_signal = False
    clean_score = 0.0

    for interval in CVD_INTERVALS:
        spot_c = fetch_cvd_candles(symbol, "spot", CVD_LOOKBACK_HOURS, interval)
        futures_c = fetch_cvd_candles(symbol, "futures", CVD_LOOKBACK_HOURS, interval)

        if not spot_c or not futures_c:
            results[f"sig_{interval}"] = False
            results[f"score_{interval}"] = 0.0
            results[f"z_spot_{interval}"] = 0.0
            results[f"z_fut_{interval}"] = 0.0
            results[f"clean_{interval}"] = False
            continue

        s_slope, s_z = compute_cvd_slope_std(spot_c)
        f_slope, f_z = compute_cvd_slope_std(futures_c)

        if s_slope is None or f_slope is None:
            results[f"sig_{interval}"] = False
            results[f"score_{interval}"] = 0.0
            results[f"z_spot_{interval}"] = 0.0
            results[f"z_fut_{interval}"] = 0.0
            results[f"clean_{interval}"] = False
            continue

        clean_div = (s_z > 1.5) and (f_z < -1.5)
        if clean_div:
            score = round(s_z * abs(f_z), 4)
            clean_score += score

        results[f"sig_{interval}"] = (s_slope > 0 and f_slope < 0)
        results[f"score_{interval}"] = round(s_slope - f_slope, 6)
        results[f"z_spot_{interval}"] = round(s_z, 2) if s_z else 0.0
        results[f"z_fut_{interval}"] = round(f_z, 2) if f_z else 0.0
        results[f"clean_{interval}"] = clean_div

        if clean_div:
            total_signal = True

    results["signal"] = total_signal
    results["clean_score"] = round(clean_score, 4)
    return results
