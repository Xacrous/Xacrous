"""Composes the backtester, the forward signal log, and win-rate blending
into the two calls the UI layer makes on every fetch/recompute: resolve
whatever's pending, and enrich+log whatever new signal just fired.
"""

from __future__ import annotations

import pandas as pd

from chartpilot.signal_engine.backtester import run_backtest
from chartpilot.signal_engine.base_strategy import BaseStrategy, Signal
from chartpilot.signal_engine.signal_log import SignalLog
from chartpilot.signal_engine.win_rate import blend_win_rate
from chartpilot.ta_engine.indicators import Mode


def resolve_pending_for_window(df: pd.DataFrame, signal_log: SignalLog | None) -> None:
    """Check the forward log's pending rows for this symbol/timeframe
    against the candles just fetched — independent of whether a new
    signal fires right now, since past signals still need grading."""
    if signal_log is None:
        return
    symbol = str(df.attrs.get("symbol", ""))
    timeframe = str(df.attrs.get("timeframe", ""))
    signal_log.resolve_pending(symbol, timeframe, df)


def enrich_signal(df: pd.DataFrame, mode: Mode, strategy: BaseStrategy, signal: Signal, signal_log: SignalLog | None) -> Signal:
    """Populate `signal.historical_win_rate` by blending a fresh backtest
    over the already-fetched `df` with the forward log's live track
    record, then log the signal for future grading."""
    backtest_result = run_backtest(df, mode, strategy)
    backtest_stats = backtest_result.win_rate_near_confidence(signal.confidence)
    forward_stats = signal_log.win_rate(strategy.id, confidence=signal.confidence) if signal_log is not None else None
    signal.historical_win_rate = blend_win_rate(backtest_stats, forward_stats)

    if signal_log is not None:
        signal_log.record_if_new(signal)
    return signal
