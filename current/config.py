"""
Configuration constants for the Short Squeeze Screener.
"""

LOSER_THRESHOLD_PCT = -10.0
MIN_VOLUME_USDT = 1_000_000
FINAL_TOP_N = 20
MAX_WORKERS = 8

# Filter mode: 'ANY' (at least one), 'ALL' (all), or 'MIN' (at least N)
LOSER_MODE = 'MIN'
MIN_LOSER_TIMEFRAMES = 2  # Minimum number of timeframes that must meet the threshold

# Intervals for CVD divergence scan
CVD_INTERVALS = ["1h", "4h", "8h", "12h", "1d"]

# Candle-count window per interval.
# Example: 1h -> 4 means the last 4 hourly candles, 1d -> 24 means the last 24 daily candles.
CVD_LOOKBACK_BY_INTERVAL = {
    "1h": 24,
    "4h": 24,
    "8h": 24,
    "12h": 24,
    "1d": 24,
}

# Standardized divergence thresholds
CVD_ZSCORE_LOOKBACK = 20
CVD_STRONG_ZSCORE = 1.5

# Relative volume settings
RELATIVE_VOLUME_LOOKBACK_DAYS = 20
RANK_WEIGHT_RELATIVE_VOLUME = 14.0

# Open interest settings
OPEN_INTEREST_LOOKBACK_POINTS = 20
OPEN_INTEREST_PERIOD = "1h"
RANK_WEIGHT_OPEN_INTEREST = 10.0

# Composite ranking weights
RANK_WEIGHT_SIGNAL_COUNT = 12.0
RANK_WEIGHT_STRONG_COUNT = 18.0
RANK_WEIGHT_RAW_DIVERGENCE = 25.0
RANK_WEIGHT_CLEAN_DIVERGENCE = 10.0
RANK_WEIGHT_LOSS_SEVERITY = 1.25
RANK_WEIGHT_LOSS_COUNT = 5.0

LOOKBACK_POINTS = [
    ("24h_ago", 1),       # 1 day ago
    ("3d_ago", 3),
    ("7d_ago", 7),
    ("30d_ago", 30),
    ("60d_ago", 60),
]

# Backward compatibility for older imports.
CVD_LOOKBACK_CANDLES = CVD_LOOKBACK_BY_INTERVAL
CVD_LOOKBACK_HOURS = CVD_LOOKBACK_BY_INTERVAL
