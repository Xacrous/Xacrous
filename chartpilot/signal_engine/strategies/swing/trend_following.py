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
from chartpilot.signal_engine.scorer import ScoreFactor
from chartpilot.signal_engine.strategies.common import build_signal, freshness_score, macd_cross_age, rsi_trigger_age
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
        macd_age = macd_cross_age(indicators.macd_line, indicators.macd_signal, bullish, _MOMENTUM_LOOKBACK)
        momentum_score: float
        momentum_desc: str
        if macd_age is not None:
            momentum_score = freshness_score(macd_age, 25.0, _MOMENTUM_LOOKBACK)
            cross_word = "bullish" if bullish else "bearish"
            momentum_desc = f"Momentum: MACD {cross_word} cross, {macd_age} candle(s) old"
        else:
            level = _RSI_RECLAIM_LEVEL if bullish else _RSI_REJECT_LEVEL
            deep = _RSI_DEEP_OVERSOLD if bullish else _RSI_DEEP_OVERBOUGHT
            rsi_age = rsi_trigger_age(indicators.rsi14, reclaim=bullish, lookback=_MOMENTUM_LOOKBACK,
                                       floor_lookback=_RSI_FLOOR_LOOKBACK, level=level, deep_extreme=deep)
            if rsi_age is None:
                return None
            momentum_score = freshness_score(rsi_age, 20.0, _MOMENTUM_LOOKBACK)
            verb = "reclaims" if bullish else "loses"
            momentum_desc = f"Momentum: RSI(14) {verb} {int(level)} from a pullback, {rsi_age} candle(s) old"

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

        return build_signal(df, "swing", self.id, bullish, price, take_profit, stop_loss, reward_risk, factors)
