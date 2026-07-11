"""Range/consolidation breakout detection and Fibonacci retracements.

Every strategy that needs a breakout (4.3, 5.5) trades the resolution of a
horizontal consolidation range rather than a sloped trendline, and the only
strategy that needs Fibonacci levels (4.2) anchors them to the most recent
qualifying impulse move — so those are the two capabilities implemented
here, both driven off the fractal swing points already computed in
`levels.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from chartpilot.ta_engine.levels import fractal_swing_points

Direction = Literal["long", "short"]


@dataclass
class RangeBreakout:
    direction: Direction
    range_high: float
    range_low: float
    breakout_price: float
    volume_ratio: float

    @property
    def range_height(self) -> float:
        return self.range_high - self.range_low

    def measured_move_target(self) -> float:
        return self.breakout_price + self.range_height if self.direction == "long" else self.breakout_price - self.range_height


def detect_range_breakout(df: pd.DataFrame, lookback: int = 20, volume_multiplier: float = 1.5) -> RangeBreakout | None:
    """A close beyond a multi-candle consolidation range on above-average
    volume — the volume confirmation is what separates a genuine breakout
    from a fakeout (Section 4.3 / 5.5).

    The range is measured over the `lookback` candles *preceding* the
    trigger candle, so the trigger candle's own high/low can't inflate the
    range it's supposedly breaking out of.
    """
    if len(df) < lookback + 1:
        return None
    window = df.iloc[-(lookback + 1):-1]
    range_high = float(window["high"].max())
    range_low = float(window["low"].min())
    trigger = df.iloc[-1]
    trigger_close = float(trigger["close"])

    avg_volume = float(window["volume"].mean())
    trigger_volume = float(trigger["volume"])
    if avg_volume <= 0:
        return None
    volume_ratio = trigger_volume / avg_volume
    if volume_ratio < volume_multiplier:
        return None

    if trigger_close > range_high:
        return RangeBreakout("long", range_high, range_low, trigger_close, volume_ratio)
    if trigger_close < range_low:
        return RangeBreakout("short", range_high, range_low, trigger_close, volume_ratio)
    return None


FIB_RATIOS = (0.236, 0.382, 0.5, 0.618, 0.786)


@dataclass
class FibRetracement:
    direction: Direction  # "long" = retracing a swing-low-to-swing-high impulse
    swing_start: float
    swing_end: float
    levels: dict[float, float]

    def level_price(self, ratio: float) -> float:
        return self.levels[ratio]

    def zone(self, low_ratio: float = 0.382, high_ratio: float = 0.618) -> tuple[float, float]:
        lo, hi = self.levels[low_ratio], self.levels[high_ratio]
        return (min(lo, hi), max(lo, hi))


def detect_fibonacci_retracement(
    df: pd.DataFrame, atr: pd.Series, lookback: int = 60, min_atr_multiple: float = 3.0, swing_order: int = 2
) -> FibRetracement | None:
    """Find the most recent qualifying impulse move within `lookback` bars
    and return its Fibonacci retracement levels, or None if no move in that
    window is large enough (ATR-normalized) to count as impulsive.
    """
    if len(df) < lookback:
        return None
    window = df.iloc[-lookback:]
    offset = len(df) - lookback
    swing_highs, swing_lows = fractal_swing_points(window, order=swing_order)
    if not swing_highs or not swing_lows:
        return None

    latest_atr = float(atr.iloc[-1])
    if latest_atr <= 0:
        return None

    last_high_idx = swing_highs[-1]
    last_low_idx = swing_lows[-1]
    high_price = float(window["high"].iloc[last_high_idx])
    low_price = float(window["low"].iloc[last_low_idx])
    move_size = high_price - low_price
    if move_size < min_atr_multiple * latest_atr:
        return None

    # direction is which extreme came *later* — that's the impulse's end
    if (offset + last_high_idx) > (offset + last_low_idx):
        direction: Direction = "long"
        swing_start, swing_end = low_price, high_price
    else:
        direction = "short"
        swing_start, swing_end = high_price, low_price

    span = swing_end - swing_start
    levels = {ratio: swing_end - span * ratio for ratio in FIB_RATIOS}
    return FibRetracement(direction=direction, swing_start=swing_start, swing_end=swing_end, levels=levels)
