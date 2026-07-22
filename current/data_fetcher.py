"""
Market data fetching with Binance-first, Bybit-fallback behavior.
"""

from datetime import timedelta
from functools import lru_cache
from typing import Optional

import certifi
import requests
import urllib3

from current.config import (
    MIN_VOLUME_USDT,
    OPEN_INTEREST_LOOKBACK_POINTS,
    OPEN_INTEREST_PERIOD,
    RELATIVE_VOLUME_LOOKBACK_DAYS,
)
from current.utils import now_utc, ts_ms


_SESSION = requests.Session()
_SESSION.trust_env = False
_SESSION.verify = certifi.where()
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_BYBIT_BASE = "https://api.bybit.com"
_BYBIT_INTERVAL_MAP = {
    "1h": "60",
    "4h": "240",
    "8h": "480",
    "12h": "720",
    "1d": "D",
}


def _get_json(url: str, params: Optional[dict] = None, timeout: int = 8, retries: int = 2):
    last_error = None
    for _ in range(retries + 1):
        try:
            response = _SESSION.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.SSLError:
            try:
                response = _SESSION.get(url, params=params, timeout=timeout, verify=False)
                response.raise_for_status()
                return response.json()
            except Exception as exc:
                last_error = exc
        except Exception as exc:
            last_error = exc
    raise last_error


def _binance_symbol_sets() -> tuple[set[str], set[str]]:
    fut_info = _get_json("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=10)
    futures_syms = {
        s["symbol"] for s in fut_info["symbols"]
        if s["quoteAsset"] == "USDT"
        and s["status"] == "TRADING"
        and s["contractType"] == "PERPETUAL"
    }

    spot_info = _get_json("https://api.binance.com/api/v3/exchangeInfo", timeout=10)
    spot_syms = {
        s["symbol"] for s in spot_info["symbols"]
        if s["quoteAsset"] == "USDT"
        and s["status"] == "TRADING"
    }
    return futures_syms, spot_syms


def _paginate_bybit_instruments(category: str) -> list[dict]:
    rows = []
    cursor = None
    while True:
        params = {"category": category, "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        payload = _get_json(f"{_BYBIT_BASE}/v5/market/instruments-info", params=params, timeout=10)
        result = payload.get("result", {})
        rows.extend(result.get("list", []))
        cursor = result.get("nextPageCursor")
        if not cursor:
            break
    return rows


@lru_cache(maxsize=1)
def _get_bybit_symbol_sets() -> tuple[set[str], set[str]]:
    linear_rows = _paginate_bybit_instruments("linear")
    linear_syms = {
        row["symbol"] for row in linear_rows
        if row.get("quoteCoin") == "USDT" and row.get("status") == "Trading"
    }

    spot_rows = _paginate_bybit_instruments("spot")
    spot_syms = {
        row["symbol"] for row in spot_rows
        if row.get("quoteCoin") == "USDT" and row.get("status") == "Trading"
    }
    return linear_syms, spot_syms


@lru_cache(maxsize=1)
def _get_bybit_linear_tickers() -> list[dict]:
    payload = _get_json(f"{_BYBIT_BASE}/v5/market/tickers", params={"category": "linear"}, timeout=10)
    return payload.get("result", {}).get("list", [])


def get_symbols() -> list:
    """
    Returns symbols available on both futures and spot with enough turnover.
    Uses Binance first, then Bybit if Binance is unavailable.
    """
    print("\n[1/4] Fetching symbol lists from Binance...")

    try:
        futures_syms, spot_syms = _binance_symbol_sets()
        common = futures_syms & spot_syms
        print(f"     {len(futures_syms)} futures | {len(spot_syms)} spot | {len(common)} on both")

        print(f"     Filtering by 24h volume > ${MIN_VOLUME_USDT:,}...")
        vol_rows = _get_json("https://fapi.binance.com/fapi/v1/ticker/24hr", timeout=10)
        vol_map = {item["symbol"]: float(item["quoteVolume"]) for item in vol_rows}
        qualified = sorted([sym for sym in common if vol_map.get(sym, 0.0) >= MIN_VOLUME_USDT])
        print(f"     {len(qualified)} tokens pass volume filter\n")
        return qualified
    except Exception as exc:
        print(f"     Binance unavailable, falling back to Bybit ({type(exc).__name__})...")

    linear_syms, spot_syms = _get_bybit_symbol_sets()
    common = linear_syms & spot_syms
    print(f"     {len(linear_syms)} linear | {len(spot_syms)} spot | {len(common)} on both")
    print(f"     Filtering by 24h volume > ${MIN_VOLUME_USDT:,}...")
    vol_map = {item["symbol"]: float(item.get("turnover24h", 0.0) or 0.0) for item in _get_bybit_linear_tickers()}
    qualified = sorted([sym for sym in common if vol_map.get(sym, 0.0) >= MIN_VOLUME_USDT])
    print(f"     {len(qualified)} tokens pass volume filter\n")
    return qualified


@lru_cache(maxsize=4096)
def _get_current_price(symbol: str) -> Optional[float]:
    try:
        payload = _get_json(
            "https://fapi.binance.com/fapi/v1/ticker/price",
            params={"symbol": symbol},
            timeout=5,
        )
        return float(payload["price"])
    except Exception:
        try:
            payload = _get_json(
                f"{_BYBIT_BASE}/v5/market/tickers",
                params={"category": "linear", "symbol": symbol},
                timeout=5,
            )
            rows = payload.get("result", {}).get("list", [])
            return float(rows[0]["lastPrice"]) if rows else None
        except Exception:
            return None


def _normalize_bybit_klines(rows: list[list]) -> list[list]:
    normalized = []
    for row in reversed(rows):
        start_ts = int(row[0])
        open_p, high_p, low_p, close_p = row[1], row[2], row[3], row[4]
        volume = row[5]
        turnover = row[6]
        normalized.append([start_ts, open_p, high_p, low_p, close_p, volume, start_ts, turnover, 0, 0, 0, 0])
    return normalized


@lru_cache(maxsize=8192)
def _get_futures_klines(symbol: str, interval: str, limit: int) -> Optional[list]:
    try:
        return _get_json(
            "https://fapi.binance.com/fapi/v1/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=8,
        )
    except Exception:
        try:
            bybit_interval = _BYBIT_INTERVAL_MAP.get(interval)
            if not bybit_interval:
                return None
            payload = _get_json(
                f"{_BYBIT_BASE}/v5/market/kline",
                params={"category": "linear", "symbol": symbol, "interval": bybit_interval, "limit": limit},
                timeout=8,
            )
            rows = payload.get("result", {}).get("list", [])
            return _normalize_bybit_klines(rows) if rows else None
        except Exception:
            return None


def get_pct_change_at(symbol: str, time_ago: float) -> Optional[float]:
    """
    Gets the % price change from a historical point to current price.
    time_ago is in days (e.g., 0.1667 = 4 hours, 3 = 3 days)
    """
    now = now_utc()
    current_price = _get_current_price(symbol)
    if current_price is None:
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

    try:
        candles = _get_futures_klines(symbol, interval, max(limit + 3, 10))
        if not candles:
            return None

        target_ts = ts_ms(target_time)
        eligible = [c for c in candles if int(c[0]) <= target_ts]
        if not eligible:
            return None

        historical_price = float(eligible[-1][4])
        if historical_price == 0:
            return None
        return round((current_price - historical_price) / historical_price * 100, 2)
    except Exception:
        return None


def get_candle_change_at(symbol: str, days_ago: int) -> Optional[float]:
    """
    Gets the 24h % price change of the daily candle that closed X days ago.
    """
    now = now_utc()
    target_time = now - timedelta(days=days_ago)

    try:
        candles = _get_futures_klines(symbol, "1d", max(days_ago + 3, 5))
        if not candles:
            return None

        target_ts = ts_ms(target_time)
        eligible = [c for c in candles if int(c[6]) <= target_ts]
        if not eligible:
            return None

        c = eligible[-1]
        open_price = float(c[1])
        close_price = float(c[4])
        if open_price == 0:
            return None
        return round((close_price - open_price) / open_price * 100, 2)
    except Exception:
        return None


def _bybit_cvd_proxy(symbol: str, market: str, hours: int, interval: str) -> Optional[list]:
    bybit_interval = _BYBIT_INTERVAL_MAP.get(interval)
    if not bybit_interval:
        return None

    category = "linear" if market == "futures" else "spot"
    payload = _get_json(
        f"{_BYBIT_BASE}/v5/market/kline",
        params={"category": category, "symbol": symbol, "interval": bybit_interval, "limit": hours + 1},
        timeout=8,
    )
    rows = payload.get("result", {}).get("list", [])
    if not rows:
        return None

    candles = []
    for row in reversed(rows[:-1]):
        open_p = float(row[1])
        high_p = float(row[2])
        low_p = float(row[3])
        close_p = float(row[4])
        total_vol = float(row[5])

        # Bybit does not expose Binance-style taker-buy kline volume.
        # Approximate buyer share from the close position within the candle range.
        candle_range = max(high_p - low_p, 1e-12)
        buyer_share = min(max((close_p - low_p) / candle_range, 0.0), 1.0)
        taker_buy_vol = total_vol * buyer_share
        candles.append((taker_buy_vol, total_vol))
    return candles


@lru_cache(maxsize=16384)
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
        data = _get_json(
            url,
            params={"symbol": symbol, "interval": interval, "limit": hours + 1},
            timeout=8,
        )[:-1]
        return [(float(c[9]), float(c[5])) for c in data]
    except Exception:
        try:
            return _bybit_cvd_proxy(symbol, market, hours, interval)
        except Exception:
            return None


@lru_cache(maxsize=1)
def _get_24h_volume_map() -> dict:
    try:
        rows = _get_json("https://fapi.binance.com/fapi/v1/ticker/24hr", timeout=10)
        return {item["symbol"]: float(item["quoteVolume"]) for item in rows}
    except Exception:
        try:
            rows = _get_bybit_linear_tickers()
            return {item["symbol"]: float(item.get("turnover24h", 0.0) or 0.0) for item in rows}
        except Exception:
            return {}


def get_relative_volume(symbol: str, lookback_days: int = RELATIVE_VOLUME_LOOKBACK_DAYS) -> dict:
    """
    Returns current 24h futures quote volume versus average historical daily quote volume.
    """
    current_quote_volume = _get_24h_volume_map().get(symbol, 0.0)
    try:
        candles = _get_futures_klines(symbol, "1d", lookback_days + 2)
        if not candles:
            return {
                "current_quote_volume": current_quote_volume,
                "avg_quote_volume": 0.0,
                "relative_volume": 0.0,
            }

        completed = candles[:-1] if len(candles) > 1 else candles
        history = completed[-lookback_days:] if len(completed) > lookback_days else completed
        avg_quote_volume = (sum(float(c[7]) for c in history) / len(history)) if history else 0.0
        relative_volume = round(current_quote_volume / avg_quote_volume, 2) if avg_quote_volume > 0 else 0.0
        return {
            "current_quote_volume": round(current_quote_volume, 2),
            "avg_quote_volume": round(avg_quote_volume, 2),
            "relative_volume": relative_volume,
        }
    except Exception:
        return {
            "current_quote_volume": round(current_quote_volume, 2),
            "avg_quote_volume": 0.0,
            "relative_volume": 0.0,
        }


def get_open_interest_metrics(
    symbol: str,
    period: str = OPEN_INTEREST_PERIOD,
    lookback_points: int = OPEN_INTEREST_LOOKBACK_POINTS,
) -> dict:
    """
    Returns current open interest and simple positioning context.
    Uses Binance first, then Bybit.
    """
    current_oi = 0.0
    current_oi_value = 0.0

    try:
        payload = _get_json(
            "https://fapi.binance.com/fapi/v1/openInterest",
            params={"symbol": symbol},
            timeout=8,
        )
        current_oi = float(payload.get("openInterest", 0.0))

        hist = _get_json(
            "https://fapi.binance.com/futures/data/openInterestHist",
            params={"symbol": symbol, "period": period, "limit": lookback_points},
            timeout=8,
        )
        if not hist:
            return {
                "open_interest": round(current_oi, 2),
                "open_interest_value": 0.0,
                "avg_open_interest_value": 0.0,
                "relative_open_interest": 0.0,
                "open_interest_change_pct": 0.0,
            }

        values = [float(row.get("sumOpenInterestValue", 0.0)) for row in hist]
        current_oi_value = values[-1] if values else 0.0
        avg_oi_value = (sum(values) / len(values)) if values else 0.0
        first_oi_value = values[0] if values else 0.0
        relative_oi = round(current_oi_value / avg_oi_value, 2) if avg_oi_value > 0 else 0.0
        oi_change_pct = round(((current_oi_value - first_oi_value) / first_oi_value) * 100, 2) if first_oi_value > 0 else 0.0

        return {
            "open_interest": round(current_oi, 2),
            "open_interest_value": round(current_oi_value, 2),
            "avg_open_interest_value": round(avg_oi_value, 2),
            "relative_open_interest": relative_oi,
            "open_interest_change_pct": oi_change_pct,
        }
    except Exception:
        pass

    try:
        ticker_payload = _get_json(
            f"{_BYBIT_BASE}/v5/market/tickers",
            params={"category": "linear", "symbol": symbol},
            timeout=8,
        )
        ticker_rows = ticker_payload.get("result", {}).get("list", [])
        if not ticker_rows:
            raise ValueError("No Bybit ticker rows")

        ticker = ticker_rows[0]
        current_oi = float(ticker.get("openInterest", 0.0) or 0.0)
        current_oi_value = float(ticker.get("openInterestValue", 0.0) or 0.0)
        last_price = float(ticker.get("lastPrice", 0.0) or 0.0)

        hist_payload = _get_json(
            f"{_BYBIT_BASE}/v5/market/open-interest",
            params={"category": "linear", "symbol": symbol, "intervalTime": period, "limit": lookback_points},
            timeout=8,
        )
        hist_rows = hist_payload.get("result", {}).get("list", [])
        if not hist_rows:
            return {
                "open_interest": round(current_oi, 2),
                "open_interest_value": round(current_oi_value, 2),
                "avg_open_interest_value": 0.0,
                "relative_open_interest": 0.0,
                "open_interest_change_pct": 0.0,
            }

        values = []
        for row in reversed(hist_rows):
            hist_oi = float(row.get("openInterest", 0.0) or 0.0)
            hist_value = hist_oi * last_price if last_price > 0 else hist_oi
            values.append(hist_value)

        avg_oi_value = (sum(values) / len(values)) if values else 0.0
        first_oi_value = values[0] if values else 0.0
        if current_oi_value == 0.0 and values:
            current_oi_value = values[-1]

        relative_oi = round(current_oi_value / avg_oi_value, 2) if avg_oi_value > 0 else 0.0
        oi_change_pct = round(((current_oi_value - first_oi_value) / first_oi_value) * 100, 2) if first_oi_value > 0 else 0.0

        return {
            "open_interest": round(current_oi, 2),
            "open_interest_value": round(current_oi_value, 2),
            "avg_open_interest_value": round(avg_oi_value, 2),
            "relative_open_interest": relative_oi,
            "open_interest_change_pct": oi_change_pct,
        }
    except Exception:
        return {
            "open_interest": 0.0,
            "open_interest_value": 0.0,
            "avg_open_interest_value": 0.0,
            "relative_open_interest": 0.0,
            "open_interest_change_pct": 0.0,
        }
