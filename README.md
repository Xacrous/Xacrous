# ChartPilot

Windows 11 desktop app for crypto technical analysis and signal generation.
ChartPilot turns live Binance spot market data into structured, rules-based,
confidence-scored trading signals. It never places, modifies, or cancels an
order — every signal is informational and requires manual action by the
user in their own exchange account. See `docs/` (or the original technical
specification) for the full design.

**Status:** Phase 1 (MVP) — REST-only data fetching, swing-mode indicators,
the Trend-Following strategy (4.1) live end-to-end, and manual-refresh chart
rendering. Remaining swing/scalp strategies, WebSocket live updates, and the
backtester are Phase 2/3 scope.

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
