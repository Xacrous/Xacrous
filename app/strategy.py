"""SMA trend-band strategy for the BTC daily chart.

Rules (evaluated on each *closed* daily candle):
  * Buy  when flat and close > SMA(n) * (1 + entry_band)
  * Sell when long and close < SMA(n) * (1 - exit_band)
  * Otherwise keep the current position (the gap between the bands is a
    hysteresis zone that stops the bot flip-flopping around the average).

Orders are placed at the next candle's open (i.e. right after the close).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Sequence


@dataclass(frozen=True)
class StrategyParams:
    sma_length: int = 100
    entry_band: float = 0.06
    exit_band: float = 0.04

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Signal:
    action: str            # "BUY", "SELL" or "HOLD"
    close: float
    sma: float
    entry_level: float
    exit_level: float
    in_position: bool      # position after applying the action
    reason: str


def sma(values: Sequence[float], length: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= length:
            total -= values[i - length]
        if i >= length - 1:
            out[i] = total / length
    return out


def evaluate(closes: Sequence[float], in_position: bool, params: StrategyParams) -> Signal:
    """Decide what to do after the last close in ``closes`` (all candles closed)."""
    if len(closes) < params.sma_length:
        raise ValueError(f"need at least {params.sma_length} closed candles, got {len(closes)}")
    avg = sum(closes[-params.sma_length:]) / params.sma_length
    close = closes[-1]
    entry = avg * (1 + params.entry_band)
    exit_ = avg * (1 - params.exit_band)
    if not in_position and close > entry:
        return Signal("BUY", close, avg, entry, exit_, True,
                      f"close {close:,.2f} above entry band {entry:,.2f}")
    if in_position and close < exit_:
        return Signal("SELL", close, avg, entry, exit_, False,
                      f"close {close:,.2f} below exit band {exit_:,.2f}")
    if in_position:
        reason = f"holding: close {close:,.2f} still above exit band {exit_:,.2f}"
    else:
        reason = f"waiting: close {close:,.2f} not above entry band {entry:,.2f}"
    return Signal("HOLD", close, avg, entry, exit_, in_position, reason)


def bands(closes: Sequence[float], params: StrategyParams) -> list[tuple[float, float, float] | None]:
    """(sma, entry_level, exit_level) per candle, None during warm-up."""
    out = []
    for m in sma(closes, params.sma_length):
        out.append(None if m is None else (m, m * (1 + params.entry_band), m * (1 - params.exit_band)))
    return out
