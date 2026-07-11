# ChartPilot

Windows 11 desktop app for crypto technical analysis and signal generation.
ChartPilot turns live Binance spot market data into structured, rules-based,
confidence-scored trading signals. It never places, modifies, or cancels an
order — every signal is informational and requires manual action by the
user in their own exchange account. See `docs/` (or the original technical
specification) for the full design.

**Status:** Phase 1 + Phase 2 — all 5 swing and 5 scalp strategies live in
the registry, WebSocket live updates (ticker + kline) layered on top of
manual refresh, pivot/Fibonacci/volume-profile chart overlays, and a
Fluent UI pass (mode toggle, watchlist, theme). Backtesting and the forward
signal log are Phase 3 scope, not yet built.

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
