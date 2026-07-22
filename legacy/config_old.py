"""
Configuration constants for the legacy Short Squeeze Screener.
"""

LOSER_THRESHOLD_PCT = -10.0
MIN_VOLUME_USDT = 1_000_000
CVD_LOOKBACK_HOURS = 24
FINAL_TOP_N = 20
MAX_WORKERS = 8

# Filter mode: 'ANY' (at least one), 'ALL' (all), or 'MIN' (at least N)
LOSER_MODE = "MIN"
MIN_LOSER_TIMEFRAMES = 1  # Legacy behavior

# Intervals for CVD divergence scan
CVD_INTERVALS = ["1h", "4h", "8h", "12h", "1d"]

LOOKBACK_POINTS = [
    ("24h_ago", 1),
    ("3d_ago", 3),
    ("7d_ago", 7),
    ("30d_ago", 30),
    ("60d_ago", 60),
]
