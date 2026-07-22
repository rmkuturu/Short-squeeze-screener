"""
Local Streamlit dashboard for the short squeeze screener.

Run locally with:
    streamlit run dashboard.py
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from current.config import CVD_INTERVALS
from current.screener import build_scan_results


st.set_page_config(page_title="Squeeze Screener", layout="wide")


RESULT_COLUMNS = [
    "symbol",
    "composite_score",
    "signal_count",
    "strong_signal_count",
    "clean_signal_count",
    "best_interval",
    "best_interval_score",
    "relative_volume",
    "relative_open_interest",
    "open_interest_change_pct",
    "24h_ago",
    "3d_ago",
    "7d_ago",
    "30d_ago",
    "60d_ago",
]


def _format_result_table(df: pd.DataFrame, top_n: int) -> pd.DataFrame:
    if df.empty:
        return df

    cols = [col for col in RESULT_COLUMNS if col in df.columns]
    table = df[cols].head(top_n).copy()
    rename_map = {
        "composite_score": "score",
        "signal_count": "sig",
        "strong_signal_count": "strong",
        "clean_signal_count": "clean",
        "best_interval": "best",
        "best_interval_score": "best_score",
        "relative_volume": "rvol",
        "relative_open_interest": "roi",
        "open_interest_change_pct": "oi_pct",
    }
    table = table.rename(columns=rename_map)
    return table


def _interval_flag_table(row: pd.Series, clean_only: bool) -> pd.DataFrame:
    prefix = "clean" if clean_only else "sig"
    label = "clean" if clean_only else "signal"
    rows = []
    for interval in CVD_INTERVALS:
        rows.append(
            {
                "interval": interval,
                label: "Y" if bool(row.get(f"{prefix}_{interval}", False)) else "-",
                "strong": "Y" if bool(row.get(f"strong_{interval}", False)) else "-",
                "score": round(float(row.get(f"score_{interval}", 0.0) or 0.0), 4),
            }
        )
    return pd.DataFrame(rows)


def _display_symbol_details(df: pd.DataFrame, clean_only: bool) -> None:
    st.subheader("Symbol Detail")
    options = df["symbol"].tolist()
    symbol = st.selectbox("Select a symbol", options, index=0)
    row = df[df["symbol"] == symbol].iloc[0]

    metric_cols = st.columns(5)
    metric_cols[0].metric("Score", f"{float(row.get('composite_score', 0.0)):.2f}")
    metric_cols[1].metric("Signals", int(row.get("signal_count", 0)))
    metric_cols[2].metric("Clean", int(row.get("clean_signal_count", 0)))
    metric_cols[3].metric("Best Interval", str(row.get("best_interval") or "-"))
    metric_cols[4].metric("RVol", f"{float(row.get('relative_volume', 0.0)):.2f}")

    return_cols = st.columns(5)
    for idx, key in enumerate(["24h_ago", "3d_ago", "7d_ago", "30d_ago", "60d_ago"]):
        value = row.get(key)
        label = key.replace("_ago", "")
        return_cols[idx].metric(label, "N/A" if pd.isna(value) else f"{float(value):+.1f}%")

    st.dataframe(_interval_flag_table(row, clean_only), use_container_width=True, hide_index=True)


def main() -> None:
    st.title("Short Squeeze Screener")
    st.caption("Local dashboard. Results stay in the app session and are not saved by the dashboard.")

    with st.sidebar:
        st.header("Controls")
        scope = st.selectbox(
            "Scan scope",
            options=[("loser_only", "Loser Only"), ("full_market", "Full Market")],
            format_func=lambda item: item[1],
        )[0]
        signal_mode = st.selectbox(
            "Signal mode",
            options=[("clean", "Clean"), ("raw", "Raw")],
            format_func=lambda item: item[1],
        )[0]
        intervals = st.multiselect(
            "Focus intervals",
            options=CVD_INTERVALS,
            default=["12h", "1d"],
            help="Optional filter. Leave empty to show all intervals.",
        )
        top_n = st.slider("Top rows to show", min_value=5, max_value=50, value=20, step=5)
        run_scan = st.button("Run Scan", type="primary", use_container_width=True)

        with st.expander("Notes"):
            st.write("- `Loser Only` is faster and usually the most useful mode.")
            st.write("- `Clean` uses stricter divergence filtering.")
            st.write("- If Binance fails, the fetcher can fall back to Bybit.")
            st.write("- Bybit CVD is an approximation because its candle API differs from Binance.")

    if run_scan:
        with st.spinner("Running scan... this can take a while on full market mode."):
            st.session_state["scan_results"] = build_scan_results(
                scope=scope,
                signal_mode=signal_mode,
                intervals=intervals,
            )

    results = st.session_state.get("scan_results")
    if not results:
        st.info("Choose your settings in the sidebar and click Run Scan.")
        return

    summary_cols = st.columns(4)
    summary_cols[0].metric("Universe", results["symbols_scanned"])
    summary_cols[1].metric("Losers", results["losers_found"])
    summary_cols[2].metric("Candidates Scanned", results["candidates_scanned"])
    summary_cols[3].metric("Results", results["result_count"])

    st.subheader("Results")
    if results["display"].empty:
        st.warning("No candidates matched the current settings.")
        return

    table = _format_result_table(results["display"], top_n=top_n)
    st.dataframe(table, use_container_width=True, hide_index=True)
    _display_symbol_details(results["ranked"], results["clean_only"])


if __name__ == "__main__":
    main()
