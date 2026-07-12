"""Historical backtester: replay a candle series through a strategy exactly
as the live app would have seen it, and score the resulting trades.

`run_backtest` walks the DataFrame bar by bar, giving the strategy the same
trailing window size the live UI fetches (`CANDLE_LIMIT`), so a backtest
and a live run are apples-to-apples. Every signal the strategy fires is
graded by `resolution.resolve_signal` against the candles that actually
followed it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from chartpilot.signal_engine.base_strategy import BaseStrategy
from chartpilot.signal_engine.resolution import Outcome, resolve_signal
from chartpilot.ta_engine.indicators import CANDLE_LIMIT, MIN_CANDLES, Mode, compute

_DEFAULT_MAX_LOOKAHEAD = 100


def _parse_iso_ms(iso_timestamp: str) -> int:
    return int(datetime.fromisoformat(iso_timestamp).timestamp() * 1000)


@dataclass
class BacktestTrade:
    entry_index: int
    entry_time_ms: int
    direction: str
    entry: float
    take_profit: float
    stop_loss: float
    confidence: int
    reward_risk_ratio: float
    outcome: Outcome
    exit_time_ms: int | None


@dataclass
class BacktestResult:
    strategy_id: str
    symbol: str
    timeframe: str
    trades: list[BacktestTrade] = field(default_factory=list)

    @property
    def resolved_trades(self) -> list[BacktestTrade]:
        return [t for t in self.trades if t.outcome in ("hit_tp", "hit_sl")]

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def win_rate(self) -> float | None:
        resolved = self.resolved_trades
        if not resolved:
            return None
        wins = sum(1 for t in resolved if t.outcome == "hit_tp")
        return wins / len(resolved)

    @property
    def avg_reward_risk(self) -> float | None:
        if not self.trades:
            return None
        return sum(t.reward_risk_ratio for t in self.trades) / len(self.trades)

    @property
    def expectancy(self) -> float | None:
        """Average R-multiple per resolved trade: a win earns +reward:risk,
        a loss costs -1R."""
        resolved = self.resolved_trades
        if not resolved:
            return None
        total = sum(t.reward_risk_ratio if t.outcome == "hit_tp" else -1.0 for t in resolved)
        return total / len(resolved)

    def win_rate_near_confidence(self, confidence: int, band: int = 15) -> tuple[float, int] | None:
        """Empirical win-rate among resolved trades within `band` points of
        `confidence` — this is what the Signal panel's blended win-rate
        draws on, so it reflects setups similar to the one on screen."""
        near = [t for t in self.resolved_trades if abs(t.confidence - confidence) <= band]
        if not near:
            return None
        wins = sum(1 for t in near if t.outcome == "hit_tp")
        return wins / len(near), len(near)


def run_backtest(df: pd.DataFrame, mode: Mode, strategy: BaseStrategy, max_lookahead: int = _DEFAULT_MAX_LOOKAHEAD) -> BacktestResult:
    """Replay every bar in `df` (from MIN_CANDLES[mode] onward), giving the
    strategy the same trailing window size the live UI fetches
    (CANDLE_LIMIT[mode]) at each step. Cost scales with len(df) — callers
    that want a quick, bounded backtest should slice `df` down first rather
    than pass a large frame and expect this to stay fast."""
    df = df.reset_index(drop=True)
    trailing_window = CANDLE_LIMIT[mode]
    min_candles = MIN_CANDLES[mode]
    symbol = str(df.attrs.get("symbol", ""))
    timeframe = str(df.attrs.get("timeframe", ""))
    n = len(df)

    trades: list[BacktestTrade] = []
    i = min_candles
    while i < n:
        window_start = max(0, i + 1 - trailing_window)
        window = df.iloc[window_start:i + 1].reset_index(drop=True)
        window.attrs["symbol"] = symbol
        window.attrs["timeframe"] = timeframe
        try:
            indicators = compute(window, mode=mode)
        except ValueError:
            i += 1
            continue

        signal = strategy.evaluate(window, indicators)
        if signal is None:
            i += 1
            continue

        expires_at_ms = _parse_iso_ms(signal.expires_at) if signal.expires_at else None
        lookahead_end = min(n, i + 1 + max_lookahead)
        future = df.iloc[i + 1:lookahead_end]
        outcome, exit_time_ms = resolve_signal(signal.direction, signal.take_profit, signal.stop_loss, expires_at_ms, future)

        trades.append(BacktestTrade(
            entry_index=i,
            entry_time_ms=int(df["open_time"].iloc[i]),
            direction=signal.direction,
            entry=signal.entry,
            take_profit=signal.take_profit,
            stop_loss=signal.stop_loss,
            confidence=signal.confidence,
            reward_risk_ratio=signal.reward_risk_ratio,
            outcome=outcome,
            exit_time_ms=exit_time_ms,
        ))

        if exit_time_ms is not None:
            matched = future.index[future["open_time"] == exit_time_ms]
            i = int(matched[0]) if len(matched) else i + 1
        else:
            i += 1

    return BacktestResult(strategy_id=strategy.id, symbol=symbol, timeframe=timeframe, trades=trades)
