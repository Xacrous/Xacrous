"""pandas-ta-classic wrapper producing a mode-specific IndicatorSet.

`compute(df, mode)` is the single entry point the rest of the app calls —
indicators are computed once per refresh here, and every strategy in the
registry re-evaluates cheap rules against the same IndicatorSet (Section
2.5's architecture note on why switching strategies doesn't re-run TA math).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd
import pandas_ta_classic as ta  # noqa: F401 — registers the df.ta accessor used below

from chartpilot.ta_engine.levels import PivotLevels, compute_pivot_points

Mode = Literal["swing", "scalp"]


@dataclass
class IndicatorSet:
    mode: Mode
    sma20: pd.Series
    sma50: pd.Series
    sma200: pd.Series
    rsi14: pd.Series
    macd_line: pd.Series
    macd_signal: pd.Series
    macd_hist: pd.Series
    atr14: pd.Series
    volume_sma20: pd.Series
    pivots: PivotLevels

    def latest(self, series: pd.Series) -> float:
        return float(series.iloc[-1])


def _compute_swing(df: pd.DataFrame) -> IndicatorSet:
    sma20 = df.ta.sma(length=20)
    sma50 = df.ta.sma(length=50)
    sma200 = df.ta.sma(length=200)
    rsi14 = df.ta.rsi(length=14)
    macd = df.ta.macd(fast=12, slow=26, signal=9)
    atr14 = df.ta.atr(length=14)
    volume_sma20 = df["volume"].rolling(window=20).mean()
    pivots = compute_pivot_points(df)

    return IndicatorSet(
        mode="swing",
        sma20=sma20,
        sma50=sma50,
        sma200=sma200,
        rsi14=rsi14,
        macd_line=macd["MACD_12_26_9"],
        macd_signal=macd["MACDs_12_26_9"],
        macd_hist=macd["MACDh_12_26_9"],
        atr14=atr14,
        volume_sma20=volume_sma20,
        pivots=pivots,
    )


def compute(df: pd.DataFrame, mode: Mode = "swing") -> IndicatorSet:
    """Compute the full indicator set for `mode`.

    Phase 1 ships swing-mode indicators only; scalp-mode (EMA/Bollinger/
    Stochastic/VWAP/Volume Profile) is Phase 2 scope per the roadmap.
    """
    if len(df) < 200:
        raise ValueError(
            f"need at least 200 candles for swing-mode indicators (SMA200), got {len(df)}"
        )
    if mode == "swing":
        return _compute_swing(df)
    raise NotImplementedError(f"mode={mode!r} indicators ship in Phase 2")
