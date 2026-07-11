"""Strategy registry: id -> Strategy instance, filtered by mode.

This is what populates the Strategy dropdown in the UI (Section 6) and is
how adding an 11th strategy later never touches UI code — register it here
and it shows up everywhere the registry is consulted.
"""

from __future__ import annotations

from chartpilot.signal_engine.base_strategy import BaseStrategy, Mode
from chartpilot.signal_engine.strategies.swing.trend_following import TrendFollowingStrategy

_STRATEGY_CLASSES: list[type[BaseStrategy]] = [
    TrendFollowingStrategy,
    # Remaining swing strategies (4.2-4.5) and all scalp strategies (5.1-5.5)
    # are Phase 2 scope per the roadmap.
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
