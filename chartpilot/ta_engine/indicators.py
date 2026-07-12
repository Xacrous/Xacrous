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

_MIN_SWING_CANDLES = 200
_MIN_SCALP_CANDLES = 50

# The trailing-window size fetched/replayed per mode — shared by the live UI
# and the backtester so a replay sees exactly what the live app would have.
# 1000 is Binance's REST klines cap per call, so this is the most history a
# single fetch can carry without pagination.
CANDLE_LIMIT: dict[Mode, int] = {"swing": 1000, "scalp": 1000}
MIN_CANDLES: dict[Mode, int] = {"swing": _MIN_SWING_CANDLES, "scalp": _MIN_SCALP_CANDLES}


@dataclass(frozen=True)
class VolumeBin:
    price_low: float
    price_high: float
    volume: float

    @property
    def price_mid(self) -> float:
        return (self.price_low + self.price_high) / 2


@dataclass
class VolumeProfile:
    bins: list[VolumeBin]

    def high_volume_nodes(self, top_n: int = 3) -> list[VolumeBin]:
        return sorted(self.bins, key=lambda b: b.volume, reverse=True)[:top_n]

    def is_near_hvn(self, price: float, top_n: int = 3, tolerance_pct: float = 0.0025) -> bool:
        for node in self.high_volume_nodes(top_n):
            tolerance = node.price_high * tolerance_pct
            if node.price_low - tolerance <= price <= node.price_high + tolerance:
                return True
        return False


def compute_volume_profile(df: pd.DataFrame, lookback: int = 100, bins: int = 20) -> VolumeProfile:
    """Bucket each candle's volume by its close price into `bins` equal-width
    buckets over the last `lookback` candles — a lightweight approximation
    of a session volume profile, good enough to locate high-volume nodes
    (HVNs) vs thin low-volume air-gaps (Section 5.2's entry filter)."""
    window = df.iloc[-lookback:]
    price_low, price_high = float(window["low"].min()), float(window["high"].max())
    if price_high <= price_low:
        return VolumeProfile(bins=[VolumeBin(price_low, price_high, float(window["volume"].sum()))])

    edges = pd.cut(window["close"], bins=bins, include_lowest=True)
    grouped = window.groupby(edges, observed=True)["volume"].sum()
    result = [
        VolumeBin(price_low=float(interval.left), price_high=float(interval.right), volume=float(volume))
        for interval, volume in grouped.items()
    ]
    return VolumeProfile(bins=result)


@dataclass
class IndicatorSet:
    mode: Mode
    rsi14: pd.Series
    atr14: pd.Series
    volume_sma20: pd.Series

    # swing-only
    sma20: pd.Series | None = None
    sma50: pd.Series | None = None
    sma200: pd.Series | None = None
    macd_line: pd.Series | None = None
    macd_signal: pd.Series | None = None
    macd_hist: pd.Series | None = None
    pivots: PivotLevels | None = None

    # scalp-only
    ema9: pd.Series | None = None
    ema21: pd.Series | None = None
    bb_lower: pd.Series | None = None
    bb_mid: pd.Series | None = None
    bb_upper: pd.Series | None = None
    bb_percent: pd.Series | None = None
    stoch_k: pd.Series | None = None
    stoch_d: pd.Series | None = None
    vwap: pd.Series | None = None
    volume_profile: VolumeProfile | None = None

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
        rsi14=rsi14,
        atr14=atr14,
        volume_sma20=volume_sma20,
        sma20=sma20,
        sma50=sma50,
        sma200=sma200,
        macd_line=macd["MACD_12_26_9"],
        macd_signal=macd["MACDs_12_26_9"],
        macd_hist=macd["MACDh_12_26_9"],
        pivots=pivots,
    )


def _compute_scalp(df: pd.DataFrame) -> IndicatorSet:
    rsi14 = df.ta.rsi(length=14)
    atr14 = df.ta.atr(length=14)
    volume_sma20 = df["volume"].rolling(window=20).mean()

    ema9 = df.ta.ema(length=9)
    ema21 = df.ta.ema(length=21)
    bbands = df.ta.bbands(length=20, std=2)
    stoch = df.ta.stoch(k=14, d=3, smooth_k=3)

    # VWAP resets per session (day); pandas-ta-classic needs a DatetimeIndex
    # to anchor the daily reset, so borrow one from open_time just for this
    # call without mutating the caller's DataFrame.
    dt_index = pd.to_datetime(df["open_time"], unit="ms")
    vwap_df = df.set_axis(dt_index, axis=0)
    vwap = vwap_df.ta.vwap()
    vwap.index = df.index

    volume_profile = compute_volume_profile(df, lookback=min(100, len(df)), bins=20)

    return IndicatorSet(
        mode="scalp",
        rsi14=rsi14,
        atr14=atr14,
        volume_sma20=volume_sma20,
        ema9=ema9,
        ema21=ema21,
        bb_lower=bbands["BBL_20_2.0"],
        bb_mid=bbands["BBM_20_2.0"],
        bb_upper=bbands["BBU_20_2.0"],
        bb_percent=bbands["BBP_20_2.0"],
        stoch_k=stoch["STOCHk_14_3_3"],
        stoch_d=stoch["STOCHd_14_3_3"],
        vwap=vwap,
        volume_profile=volume_profile,
    )


def compute(df: pd.DataFrame, mode: Mode = "swing") -> IndicatorSet:
    """Compute the full indicator set for `mode`."""
    if mode == "swing":
        if len(df) < _MIN_SWING_CANDLES:
            raise ValueError(
                f"need at least {_MIN_SWING_CANDLES} candles for swing-mode indicators (SMA200), got {len(df)}"
            )
        return _compute_swing(df)
    if mode == "scalp":
        if len(df) < _MIN_SCALP_CANDLES:
            raise ValueError(
                f"need at least {_MIN_SCALP_CANDLES} candles for scalp-mode indicators, got {len(df)}"
            )
        return _compute_scalp(df)
    raise ValueError(f"unknown mode {mode!r}")
