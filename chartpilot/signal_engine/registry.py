"""Strategy registry: id -> Strategy instance, filtered by mode.

This is what populates the Strategy dropdown in the UI (Section 6) and is
how adding an 11th strategy later never touches UI code — register it here
and it shows up everywhere the registry is consulted.
"""

from __future__ import annotations

from chartpilot.signal_engine.base_strategy import BaseStrategy, Mode
from chartpilot.signal_engine.strategies.scalp.bollinger_reversion import BollingerReversionStrategy
from chartpilot.signal_engine.strategies.scalp.breakout_momentum import BreakoutMomentumScalpStrategy
from chartpilot.signal_engine.strategies.scalp.ema_crossover import EmaCrossoverStrategy
from chartpilot.signal_engine.strategies.scalp.range_scalp import RangeScalpStrategy
from chartpilot.signal_engine.strategies.scalp.vwap_reversion import VwapReversionStrategy
from chartpilot.signal_engine.strategies.swing.breakout import BreakoutStrategy
from chartpilot.signal_engine.strategies.swing.macd_momentum import MacdMomentumStrategy
from chartpilot.signal_engine.strategies.swing.pullback_fib import PullbackFibStrategy
from chartpilot.signal_engine.strategies.swing.support_resistance import SupportResistanceReversalStrategy
from chartpilot.signal_engine.strategies.swing.trend_following import TrendFollowingStrategy
from chartpilot.signal_engine.strategies.trade.vwap_crossover import VwapCrossoverStrategy

_STRATEGY_CLASSES: list[type[BaseStrategy]] = [
    TrendFollowingStrategy,
    PullbackFibStrategy,
    BreakoutStrategy,
    SupportResistanceReversalStrategy,
    MacdMomentumStrategy,
    EmaCrossoverStrategy,
    BollingerReversionStrategy,
    VwapReversionStrategy,
    RangeScalpStrategy,
    BreakoutMomentumScalpStrategy,
    VwapCrossoverStrategy,
]

_REGISTRY: dict[str, BaseStrategy] = {cls.id: cls() for cls in _STRATEGY_CLASSES}


def get_strategy(strategy_id: str) -> BaseStrategy:
    try:
        return _REGISTRY[strategy_id]
    except KeyError as exc:
        raise KeyError(f"no strategy registered with id {strategy_id!r}") from exc


def list_strategies(mode: Mode | None = None) -> list[BaseStrategy]:
    strategies = list(_REGISTRY.values())
    if mode is not None:
        strategies = [s for s in strategies if s.mode == mode]
    return strategies


def default_strategy(mode: Mode) -> BaseStrategy:
    candidates = list_strategies(mode)
    if not candidates:
        raise KeyError(f"no strategies registered for mode {mode!r}")
    return candidates[0]
