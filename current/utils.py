"""
Utility functions for the Short Squeeze Screener.
"""

from datetime import datetime, timezone


def ts_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
