# Short Squeeze Screener

Short Squeeze Screener is a Python-based market scanner for finding squeeze-style setups in liquid USDT pairs by combining:

- historical underperformance
- spot vs futures CVD divergence
- relative volume
- open interest expansion

The project supports both a CLI workflow and a local Streamlit dashboard.

## Core Concept

The main idea is to find symbols that:

1. have already been weak or heavily sold off across multiple historical checkpoints
2. are now showing improving buying pressure in spot
3. while futures positioning still leans the other way

That combination can highlight cases where:

- spot demand is starting to absorb supply
- the market is still positioned skeptically in derivatives
- a squeeze or sharp repricing becomes more likely if positioning gets trapped

In the codebase, the divergence signal is defined as:

- spot CVD slope `> 0`
- futures CVD slope `< 0`

This is treated as a bullish squeeze-style divergence, not a guaranteed trade signal.

## How The Screener Works

The scanner runs in four broad steps:

1. Build the liquid universe from symbols that trade on both spot and perpetual futures and pass the minimum turnover filter.
2. Filter for historical losers, or scan the full market if full-market mode is selected.
3. Measure multi-timeframe CVD divergence between spot and futures.
4. Rank candidates with a composite score.

## Universe Filter

The tradable universe is built from symbols that:

- exist on both spot and perpetual futures
- are quoted in `USDT`
- have at least `$1,000,000` in daily quote volume

The fetcher uses Binance first and falls back to Bybit if Binance is unavailable.

## Historical Loser Filter

Default historical checkpoints:

- `24h_ago`
- `3d_ago`
- `7d_ago`
- `30d_ago`
- `60d_ago`

Default threshold:

- `LOSER_THRESHOLD_PCT = -10.0`

Default qualification mode:

- `LOSER_MODE = "MIN"`
- `MIN_LOSER_TIMEFRAMES = 2`

That means a symbol qualifies as a historical loser when at least 2 configured lookback points are `-10%` or worse.

### Formula: Current-Price Loss Method

For each lookback point:

```text
pct_change = ((current_price - historical_price) / historical_price) * 100
```

The screener stores:

- `qualified_count`: number of timeframes that meet the threshold
- `worst_loss`: most negative return across checkpoints
- `avg_return`: average return across valid checkpoints

### Formula: Old Daily-Candle Method

The legacy method also exists in the repo:

```text
candle_change = ((daily_close - daily_open) / daily_open) * 100
```

This uses the daily candle that closed at the selected historical point.

## CVD Divergence Logic

CVD here is built from taker-buy volume and total volume.

### Formula: Candle Delta

```text
delta = 2 * taker_buy_volume - total_volume
```

This is equivalent to:

```text
delta = taker_buy_volume - seller_volume
```

because:

```text
seller_volume = total_volume - taker_buy_volume
```

### Formula: CVD

```text
CVD_t = cumulative sum of delta over the selected window
```

### Formula: Normalized CVD Slope

For each interval, the screener runs a linear regression slope on the CVD series and normalizes it by average volume:

```text
normalized_cvd_slope = slope(CVD series) / average_volume
```

This makes slope values more comparable across symbols with different size and turnover.

### Raw Divergence Condition

For a symbol and interval:

```text
raw_signal = (spot_cvd_slope > 0) and (futures_cvd_slope < 0)
```

### Raw Divergence Score

For each interval:

```text
interval_score = spot_cvd_slope - futures_cvd_slope
```

Across all intervals:

```text
raw_divergence_score = sum(max(interval_score, 0))
```

### Z-Score Standardization

The screener also measures whether each slope is statistically unusual relative to recent history:

```text
z_score = (current_slope - mean(previous_slopes)) / std(previous_slopes)
```

Default settings:

- `CVD_ZSCORE_LOOKBACK = 20`
- `CVD_STRONG_ZSCORE = 1.5`

### Clean Divergence Condition

A divergence is considered clean or statistically strong when:

```text
spot_slope > 0
futures_slope < 0
spot_zscore >= 1.5
futures_zscore <= -1.5
```

### Clean Divergence Score

Per interval:

```text
clean_score_interval = spot_zscore * abs(futures_zscore)
```

Across all intervals:

```text
clean_score = sum(spot_zscore * abs(futures_zscore) for clean intervals only)
```

## Intervals and Windows

Default scan intervals:

- `1h`
- `4h`
- `8h`
- `12h`
- `1d`

Default lookback window per interval:

- `24` candles for each interval

## Relative Volume

Relative volume compares current 24h futures quote volume against recent daily average quote volume.

### Formula

```text
relative_volume = current_24h_quote_volume / average_daily_quote_volume
```

Default lookback:

- `RELATIVE_VOLUME_LOOKBACK_DAYS = 20`

Interpretation:

- `1.0` means current volume is near average
- `2.0` means current volume is about 2x average

## Open Interest Metrics

The screener also adds derivatives positioning context.

### Formula: Relative Open Interest

```text
relative_open_interest = current_open_interest_value / average_open_interest_value
```

### Formula: Open Interest Change

```text
open_interest_change_pct = ((current_oi_value - first_oi_value) / first_oi_value) * 100
```

Default settings:

- `OPEN_INTEREST_PERIOD = "1h"`
- `OPEN_INTEREST_LOOKBACK_POINTS = 20`

## Composite Ranking Formula

The scanner produces a final `composite_score` for ranking.

### Raw Mode

```text
composite_score =
    signal_count * 12.0
  + strong_signal_count * 18.0
  + raw_divergence_score * 25.0
  + clean_score * 10.0
  + relative_volume * 14.0
  + relative_open_interest * 10.0
  + max(open_interest_change_pct, 0) * 0.6
  + abs(worst_loss) * 1.25
  + qualified_count * 5.0
```

### Clean Mode

```text
composite_score =
    clean_signal_count * 26.0
  + clean_score * 16.0
  + strong_signal_count * 6.0
  + raw_divergence_score * 8.0
  + relative_volume * 18.0
  + relative_open_interest * 12.0
  + max(open_interest_change_pct, 0) * 0.8
  + abs(worst_loss) * 0.35
  + qualified_count * 2.0
```

These formulas come directly from the current implementation in `current/screener.py` and `current/config.py`.

## Outputs

The project can produce:

- ranked CLI tables
- CSV files under `results/`
- a local Streamlit dashboard with symbol-level drilldown

Key columns include:

- `composite_score`
- `signal_count`
- `strong_signal_count`
- `clean_signal_count`
- `best_interval`
- `best_interval_score`
- `relative_volume`
- `relative_open_interest`
- `open_interest_change_pct`

## Dashboard

Run the local dashboard with:

```bash
streamlit run dashboard.py
```

Dashboard options include:

- `Loser Only` vs `Full Market`
- `Clean` vs `Raw`
- interval filtering
- top-N result display

## CLI Usage

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the screener:

```bash
python main.py
```

## Important Notes

- This is a screening and ranking tool, not an execution system.
- A high score does not guarantee a squeeze.
- Bybit fallback uses an approximation for taker-buy pressure because its candle API differs from Binance.
- Full-market scans are slower and more expensive in API usage than loser-only scans.
- Every candidate should still be confirmed on chart before taking risk.

## Repo Structure

```text
current/      active screener logic
legacy/       older reference implementation
dashboard.py  Streamlit dashboard
main.py       CLI entry point
results/      exported CSV results
```
