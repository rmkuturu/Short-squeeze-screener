"""
Short Squeeze Screener - Entry Point
=====================================
Filter 1: Coins that were strong losers at multiple historical points:
           24h ago, 3d ago, 7d ago, 30d ago, 60d ago (-10% or worse)

Filter 2: Multi-timeframe CVD divergence:
           Spot CVD trending UP + Futures CVD trending DOWN

Filter 3: Stronger ranking using:
          number of signals, divergence strength, and loss severity

Output: Ranked shortlists saved to CSV

Install:
  pip install -r requirements.txt

Run:
  python main.py
"""

from current.screener import run_screener

if __name__ == "__main__":
    run_screener()
