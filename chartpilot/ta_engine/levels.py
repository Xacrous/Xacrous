"""Pivot points and fractal-based support/resistance clustering."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class PivotLevels:
    pp: float
    r1: float
    r2: float
    r3: float
    s1: float
    s2: float
    s3: float

    def as_dict(self) -> dict[str, float]:
        return {"pp": self.pp, "r1": self.r1, "r2": self.r2, "r3": self.r3,
                "s1": self.s1, "s2": self.s2, "s3": self.s3}

    def sorted_levels(self) -> list[float]:
        return sorted(self.as_dict().values())

    def nearest_level(self, price: float) -> float:
        return min(self.sorted_levels(), key=lambda lvl: abs(lvl - price))


def compute_pivot_points(df: pd.DataFrame) -> PivotLevels:
    """Standard (floor-trader) pivot points from the most recent *completed* candle.

    df must have at least 2 rows; the last row is treated as the in-progress
    candle and the second-to-last as the completed reference period.
    """
    if len(df) < 2:
        raise ValueError("need at least 2 candles to compute pivot points")
    ref = df.iloc[-2]
    high, low, close = float(ref["high"]), float(ref["low"]), float(ref["close"])
    pp = (high + low + close) / 3.0
    r1 = 2 * pp - low
    s1 = 2 * pp - high
    r2 = pp + (high - low)
    s2 = pp - (high - low)
    r3 = high + 2 * (pp - low)
    s3 = low - 2 * (high - pp)
    return PivotLevels(pp=pp, r1=r1, r2=r2, r3=r3, s1=s1, s2=s2, s3=s3)


def fractal_swing_points(df: pd.DataFrame, order: int = 2) -> tuple[list[int], list[int]]:
    """Fractal swing highs/lows: a bar is a swing high/low if it's more
    extreme than the `order` bars on either side (Section 3's definition).

    Returns (swing_high_indices, swing_low_indices) as positional indices
    into df.
    """
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    n = len(df)
    swing_highs: list[int] = []
    swing_lows: list[int] = []
    for i in range(order, n - order):
        window_highs = highs[i - order:i + order + 1]
        if highs[i] == window_highs.max() and (window_highs == highs[i]).sum() == 1:
            swing_highs.append(i)
        window_lows = lows[i - order:i + order + 1]
        if lows[i] == window_lows.min() and (window_lows == lows[i]).sum() == 1:
            swing_lows.append(i)
    return swing_highs, swing_lows


def most_recent_swing_low(df: pd.DataFrame, order: int = 2) -> float | None:
    _, swing_lows = fractal_swing_points(df, order=order)
    if not swing_lows:
        return None
    return float(df["low"].iloc[swing_lows[-1]])


def most_recent_swing_high(df: pd.DataFrame, order: int = 2) -> float | None:
    swing_highs, _ = fractal_swing_points(df, order=order)
    if not swing_highs:
        return None
    return float(df["high"].iloc[swing_highs[-1]])
