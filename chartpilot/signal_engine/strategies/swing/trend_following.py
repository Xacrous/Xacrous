"""4.1 Trend-Following (Moving Average Crossover) — the default swing strategy.

Ride the intermediate trend, don't fight it. All four entry conditions
(trend structure, momentum trigger, S/R location, volume confirmation) are
gating requirements — a signal only fires when every one holds — and the
confidence score (Trend 30 / Momentum 25 / Location 25 / Volume 20) then
reflects how *strongly* each condition is met, not just whether it passed.
"""

from __future__ import annotations

import pandas as pd

from chartpilot.signal_engine.base_strategy import BaseStrategy, Signal
from chartpilot.signal_engine.scorer import ScoreFactor, rationale_lines, total_confidence
from chartpilot.ta_engine.indicators import IndicatorSet
from chartpilot.ta_engine.levels import most_recent_swing_high, most_recent_swing_low

_MOMENTUM_LOOKBACK = 5
_RSI_FLOOR_LOOKBACK = 10
_RSI_RECLAIM_LEVEL = 40.0
_RSI_REJECT_LEVEL = 60.0
_RSI_DEEP_OVERSOLD = 20.0
_RSI_DEEP_OVERBOUGHT = 80.0
_LOCATION_TOLERANCE_PCT = 0.01
_MIN_REWARD_RISK = 1.5
_TREND_PERSISTENCE_CAP = 20


def _freshness_score(age: int, max_score: float, lookback: int) -> float:
    """Fresher triggers score higher: 100% of max_score at age 0, decaying
    to 50% of max_score at the edge of the lookback window."""
    frac = 1.0 - (age / lookback) * 0.5
    return round(max_score * frac, 1)


def _macd_cross_age(macd_line: pd.Series, macd_signal: pd.Series, bullish: bool, lookback: int) -> int | None:
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


def _rsi_trigger_age(rsi: pd.Series, reclaim: bool, lookback: int, floor_lookback: int) -> int | None:
    """Age of an RSI reclaim/reject trigger, or None if not present or if
    it originates from a deep-oversold/overbought extreme (a different,
    stronger reversal setup rather than a shallow pullback)."""
    n = len(rsi)
    level = _RSI_RECLAIM_LEVEL if reclaim else _RSI_REJECT_LEVEL
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
        deep = _RSI_DEEP_OVERSOLD if reclaim else _RSI_DEEP_OVERBOUGHT
        if reclaim and extreme < deep:
            return None
        if not reclaim and extreme > deep:
            return None
        return age
    return None


def _trend_persistence(close: pd.Series, sma50: pd.Series, sma200: pd.Series, bullish: bool) -> int:
    count = 0
    n = len(close)
    for i in range(n - 1, -1, -1):
        ok = (close.iloc[i] > sma50.iloc[i] > sma200.iloc[i]) if bullish else (close.iloc[i] < sma50.iloc[i] < sma200.iloc[i])
        if not ok:
            break
        count += 1
        if count >= _TREND_PERSISTENCE_CAP:
            break
    return count


class TrendFollowingStrategy(BaseStrategy):
    id = "trend_following_ma_cross"
    display_name = "Trend-Following (MA Cross)"
    mode = "swing"
    required_indicators = ["sma50", "sma200", "rsi14", "macd_line", "macd_signal", "atr14", "volume_sma20", "pivots"]

    def evaluate(self, df: pd.DataFrame, indicators: IndicatorSet) -> Signal | None:
        long_signal = self._evaluate_direction(df, indicators, bullish=True)
        if long_signal is not None:
            return long_signal
        return self._evaluate_direction(df, indicators, bullish=False)

    def _evaluate_direction(self, df: pd.DataFrame, indicators: IndicatorSet, bullish: bool) -> Signal | None:
        close = df["close"]
        price = float(close.iloc[-1])

        # Gate 1: trend structure
        trend_ok = (price > indicators.latest(indicators.sma50) > indicators.latest(indicators.sma200)) if bullish \
            else (price < indicators.latest(indicators.sma50) < indicators.latest(indicators.sma200))
        if not trend_ok:
            return None

        # Gate 2: momentum trigger (MACD cross preferred, RSI reclaim/reject as fallback)
        macd_age = _macd_cross_age(indicators.macd_line, indicators.macd_signal, bullish, _MOMENTUM_LOOKBACK)
        momentum_score: float
        momentum_desc: str
        if macd_age is not None:
            momentum_score = _freshness_score(macd_age, 25.0, _MOMENTUM_LOOKBACK)
            cross_word = "bullish" if bullish else "bearish"
            momentum_desc = f"Momentum: MACD {cross_word} cross, {macd_age} candle(s) old"
        else:
            rsi_age = _rsi_trigger_age(indicators.rsi14, reclaim=bullish, lookback=_MOMENTUM_LOOKBACK, floor_lookback=_RSI_FLOOR_LOOKBACK)
            if rsi_age is None:
                return None
            momentum_score = _freshness_score(rsi_age, 20.0, _MOMENTUM_LOOKBACK)
            level = int(_RSI_RECLAIM_LEVEL) if bullish else int(_RSI_REJECT_LEVEL)
            verb = "reclaims" if bullish else "loses"
            momentum_desc = f"Momentum: RSI(14) {verb} {level} from a pullback, {rsi_age} candle(s) old"

        # Gate 3: location — within tolerance of a pivot/S-R cluster
        nearest_pivot = indicators.pivots.nearest_level(price)
        distance_pct = abs(price - nearest_pivot) / price
        if distance_pct > _LOCATION_TOLERANCE_PCT:
            return None
        location_score = 25.0 * max(0.4, 1.0 - distance_pct / _LOCATION_TOLERANCE_PCT)
        swing_ref = most_recent_swing_low(df) if bullish else most_recent_swing_high(df)
        location_desc = "Location: at swing-low support / pivot cluster" if bullish else "Location: at swing-high resistance / pivot cluster"
        if swing_ref is not None and abs(price - swing_ref) / price <= _LOCATION_TOLERANCE_PCT * 1.5:
            location_score = min(25.0, location_score + 3.0)

        # Gate 4: volume confirmation
        trigger_volume = float(df["volume"].iloc[-1])
        avg_volume = indicators.latest(indicators.volume_sma20)
        if avg_volume <= 0 or trigger_volume < avg_volume:
            return None
        volume_ratio = trigger_volume / avg_volume
        volume_score = min(20.0, 10.0 + (volume_ratio - 1.0) * 20.0)
        volume_desc = f"Volume: {volume_ratio:.1f}x 20-period average"

        # Trend sub-score: reward persistence of the stacked SMA structure
        persistence = _trend_persistence(close, indicators.sma50, indicators.sma200, bullish)
        trend_score = 30.0 * min(1.0, 0.6 + 0.4 * (persistence / _TREND_PERSISTENCE_CAP))
        trend_desc = (
            f"Trend: price above SMA50 above SMA200 ({persistence} candles)" if bullish
            else f"Trend: price below SMA50 below SMA200 ({persistence} candles)"
        )

        # Exit levels
        atr = indicators.latest(indicators.atr14)
        if bullish:
            swing_low = most_recent_swing_low(df)
            stop_loss = (swing_low if swing_low is not None else price - 2 * atr) - atr
            take_profit = min((lvl for lvl in indicators.pivots.sorted_levels() if lvl > price), default=price + 2 * atr)
        else:
            swing_high = most_recent_swing_high(df)
            stop_loss = (swing_high if swing_high is not None else price + 2 * atr) + atr
            take_profit = max((lvl for lvl in indicators.pivots.sorted_levels() if lvl < price), default=price - 2 * atr)

        risk = abs(price - stop_loss)
        reward = abs(take_profit - price)
        if risk <= 0:
            return None
        reward_risk = reward / risk
        if reward_risk < _MIN_REWARD_RISK:
            return None

        factors = [
            ScoreFactor(trend_desc, round(trend_score, 1), 30.0),
            ScoreFactor(momentum_desc, round(momentum_score, 1), 25.0),
            ScoreFactor(location_desc, round(location_score, 1), 25.0),
            ScoreFactor(volume_desc, round(volume_score, 1), 20.0),
        ]

        return Signal(
            symbol=str(df.attrs.get("symbol", "")),
            timeframe=str(df.attrs.get("timeframe", "")),
            mode="swing",
            strategy=self.id,
            direction="long" if bullish else "short",
            entry=round(price, 8),
            take_profit=round(float(take_profit), 8),
            stop_loss=round(float(stop_loss), 8),
            confidence=total_confidence(factors),
            reward_risk_ratio=round(reward_risk, 2),
            rationale=rationale_lines(factors),
        )
