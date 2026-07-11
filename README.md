# ChartPilot

Windows 11 desktop app for crypto technical analysis and signal generation.
ChartPilot turns live Binance spot market data into structured, rules-based,
confidence-scored trading signals. It never places, modifies, or cancels an
order — every signal is informational and requires manual action by the
user in their own exchange account. See `docs/` (or the original technical
specification) for the full design.

**Status:** Phase 1 + 2 + 3 — all 5 swing and 5 scalp strategies live in the
registry, WebSocket live updates (ticker + kline), pivot/Fibonacci/volume-
profile chart overlays, a Fluent UI pass (mode toggle, watchlist, theme),
a walk-forward historical backtester, and a forward signal log that
auto-resolves every real signal against new candle data. Every generated
signal's `historical_win_rate` blends the two. The Backtest/History screen
runs replays and exports the signal log to CSV.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate   # .venv\Scripts\activate on Windows
pip install -e ".[dev]"
```

## Run

```bash
python -m chartpilot.main
```

## Test

```bash
pytest
```
