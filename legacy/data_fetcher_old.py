"""
Binance API data fetching functions for the legacy screener.
"""

from datetime import timedelta
from typing import Optional

import requests

from legacy.config_old import MIN_VOLUME_USDT
from legacy.utils_old import now_utc, ts_ms


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


def get_pct_change_at(symbol: str, time_ago: float) -> Optional[float]:
    """
    Gets the % price change from a historical point to current price.
    time_ago is in days (e.g., 0.1667 = 4 hours, 3 = 3 days)
    """
    now = now_utc()

    try:
        r = requests.get(
            f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={symbol}",
            timeout=5,
        )
        if r.status_code != 200:
            return None
        current_price = float(r.json()["price"])
    except Exception:
        return None

    if time_ago < 1:
        hours_ago = int(time_ago * 24)
        target_time = now - timedelta(hours=hours_ago)
        interval = "1h"
        limit = hours_ago + 1
    else:
        days_ago = int(time_ago)
        target_time = now - timedelta(days=days_ago)
        interval = "1d"
        limit = days_ago + 1

    params = {
        "symbol": symbol,
        "interval": interval,
        "endTime": ts_ms(target_time),
        "limit": limit,
    }
    try:
        r = requests.get(
            "https://fapi.binance.com/fapi/v1/klines",
            params=params,
            timeout=8,
        )
        if r.status_code != 200:
            return None

        candles = r.json()
        if not candles:
            return None

        historical_price = float(candles[-1][4])
        if historical_price == 0:
            return None

        return round((current_price - historical_price) / historical_price * 100, 2)
    except Exception:
        return None


def get_candle_change_at(symbol: str, days_ago: int) -> Optional[float]:
    """
    Legacy method: daily candle change at X days ago.
    """
    now = now_utc()
    target_time = now - timedelta(days=days_ago)

    params = {
        "symbol": symbol,
        "interval": "1d",
        "endTime": ts_ms(target_time),
        "limit": 1,
    }
    try:
        r = requests.get(
            "https://fapi.binance.com/fapi/v1/klines",
            params=params,
            timeout=8,
        )
        if r.status_code != 200:
            return None

        candles = r.json()
        if not candles:
            return None

        candle = candles[0]
        open_price = float(candle[1])
        close_price = float(candle[4])
        if open_price == 0:
            return None

        return round((close_price - open_price) / open_price * 100, 2)
    except Exception:
        return None


def fetch_cvd_candles(symbol: str, market: str, hours: int, interval: str = "1h") -> Optional[list]:
    """
    Fetches last N completed candles for a specific interval.
    Returns list of (taker_buy_vol, total_vol) tuples.
    """
    url = (
        "https://fapi.binance.com/fapi/v1/klines" if market == "futures"
        else "https://api.binance.com/api/v3/klines"
    )
    try:
        r = requests.get(
            url,
            params={"symbol": symbol, "interval": interval, "limit": hours + 1},
            timeout=8,
        )
        if r.status_code != 200:
            print(f"      [Error] {symbol} {interval} {market}: {r.status_code} - {r.text}")
            return None
        r.raise_for_status()
        data = r.json()[:-1]
        return [(float(c[9]), float(c[5])) for c in data]
    except Exception:
        return None
