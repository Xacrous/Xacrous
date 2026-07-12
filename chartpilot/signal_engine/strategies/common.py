"""Shared helpers used across multiple strategies: freshness scoring,
MACD/RSI trigger detection, candlestick reversal checks, and slope."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from chartpilot.data_fetcher.exchange_client import timeframe_to_seconds
from chartpilot.signal_engine.base_strategy import Signal
from chartpilot.signal_engine.scorer import ScoreFactor, rationale_lines, total_confidence


def compute_expiry(df: pd.DataFrame, timeframe: str, candles: int) -> str:
    """Scalp setups decay fast: a signal stales once `candles` timeframe
    intervals pass from the triggering candle without TP or SL hit."""
    last_open_ms = int(df["open_time"].iloc[-1])
    last_open = datetime.fromtimestamp(last_open_ms / 1000, tz=timezone.utc)
    expiry = last_open + timedelta(seconds=timeframe_to_seconds(timeframe) * (candles + 1))
    return expiry.isoformat()


def freshness_score(age: int, max_score: float, lookback: int) -> float:
    """Fresher triggers score higher: 100% of max_score at age 0, decaying
    to 50% of max_score at the edge of the lookback window."""
    frac = 1.0 - (age / lookback) * 0.5
    return round(max_score * frac, 1)


def macd_cross_age(macd_line: pd.Series, macd_signal: pd.Series, bullish: bool, lookback: int) -> int | None:
    n = len(macd_line)
    for age in range(lookback + 1):
        i = n - 1 - age
        if i < 1:
            break
        prev_diff = macd_line.iloc[i - 1] - macd_signal.iloc[i - 1]
        curr_diff = macd_line.iloc[i] - macd_signal.iloc[i]
        crossed = (prev_diff <= 0 < curr_diff) if bullish else (prev_diff >= 0 > curr_diff)
        if crossed:
            return age
    return None


def rsi_trigger_age(
    rsi: pd.Series, reclaim: bool, lookback: int, floor_lookback: int,
    level: float, deep_extreme: float,
) -> int | None:
    """Age of an RSI reclaim/reject trigger, or None if not present or if
    it originates from a deep-oversold/overbought extreme (a different,
    stronger reversal setup rather than a shallow pullback)."""
    n = len(rsi)
    for age in range(lookback + 1):
        i = n - 1 - age
        if i < 1:
            break
        prev_val, curr_val = rsi.iloc[i - 1], rsi.iloc[i]
        crossed = (prev_val < level <= curr_val) if reclaim else (prev_val > level >= curr_val)
        if not crossed:
            continue
        floor_start = max(0, i - floor_lookback)
        recent_window = rsi.iloc[floor_start:i]
        if recent_window.empty:
            return age
        extreme = recent_window.min() if reclaim else recent_window.max()
        if reclaim and extreme < deep_extreme:
            return None
        if not reclaim and extreme > deep_extreme:
            return None
        return age
    return None


def slope(series: pd.Series, lookback: int) -> float:
    """Simple recent-vs-past slope: positive means rising."""
    if len(series) <= lookback:
        return 0.0
    return float(series.iloc[-1] - series.iloc[-1 - lookback])


def is_bullish_engulfing(df: pd.DataFrame) -> bool:
    if len(df) < 2:
        return False
    prev, curr = df.iloc[-2], df.iloc[-1]
    prev_bearish = prev["close"] < prev["open"]
    curr_bullish = curr["close"] > curr["open"]
    engulfs = curr["open"] <= prev["close"] and curr["close"] >= prev["open"]
    return bool(prev_bearish and curr_bullish and engulfs)


def is_bearish_engulfing(df: pd.DataFrame) -> bool:
    if len(df) < 2:
        return False
    prev, curr = df.iloc[-2], df.iloc[-1]
    prev_bullish = prev["close"] > prev["open"]
    curr_bearish = curr["close"] < curr["open"]
    engulfs = curr["open"] >= prev["close"] and curr["close"] <= prev["open"]
    return bool(prev_bullish and curr_bearish and engulfs)


def build_signal(
    df: pd.DataFrame,
    mode: str,
    strategy_id: str,
    bullish: bool,
    price: float,
    take_profit: float,
    stop_loss: float,
    reward_risk: float,
    factors: list[ScoreFactor],
    expiry_candles: int | None = None,
) -> Signal:
    timeframe = str(df.attrs.get("timeframe", ""))
    expires_at = compute_expiry(df, timeframe, expiry_candles) if expiry_candles is not None else None
    return Signal(
        symbol=str(df.attrs.get("symbol", "")),
        timeframe=timeframe,
        mode=mode,
        strategy=strategy_id,
        direction="long" if bullish else "short",
        entry=round(price, 8),
        take_profit=round(float(take_profit), 8),
        stop_loss=round(float(stop_loss), 8),
        confidence=total_confidence(factors),
        reward_risk_ratio=round(reward_risk, 2),
        rationale=rationale_lines(factors),
        expires_at=expires_at,
    )
