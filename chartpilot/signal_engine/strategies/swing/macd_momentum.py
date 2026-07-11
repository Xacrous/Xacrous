"""4.5 MACD Momentum Crossover — a momentum-first variant.

Enters on a fresh MACD signal rather than waiting for a location/pullback
trigger, filtered to trade only in the direction of the SMA200 slope so it
never fades the dominant trend. Trades more frequently than 4.1-4.4 but
with a lower average reward:risk.
"""

from __future__ import annotations

import pandas as pd

from chartpilot.signal_engine.base_strategy import BaseStrategy, Signal
from chartpilot.signal_engine.scorer import ScoreFactor
from chartpilot.signal_engine.strategies.common import build_signal, freshness_score, macd_cross_age, slope
from chartpilot.ta_engine.indicators import IndicatorSet

_MOMENTUM_LOOKBACK = 3
_SMA200_SLOPE_LOOKBACK = 10
_FIXED_REWARD_RISK = 2.0
_MIN_REWARD_RISK = 1.5


class MacdMomentumStrategy(BaseStrategy):
    id = "macd_momentum"
    display_name = "MACD Momentum Crossover"
    mode = "swing"
    required_indicators = ["macd_line", "macd_signal", "macd_hist", "sma200", "atr14", "volume_sma20", "pivots"]

    def evaluate(self, df: pd.DataFrame, indicators: IndicatorSet) -> Signal | None:
        long_signal = self._evaluate_direction(df, indicators, bullish=True)
        if long_signal is not None:
            return long_signal
        return self._evaluate_direction(df, indicators, bullish=False)

    def _evaluate_direction(self, df: pd.DataFrame, indicators: IndicatorSet, bullish: bool) -> Signal | None:
        price = float(df["close"].iloc[-1])

        # Gate 1: only trade in the direction of the SMA200 slope
        sma200_slope = slope(indicators.sma200, _SMA200_SLOPE_LOOKBACK)
        if bullish and sma200_slope <= 0:
            return None
        if not bullish and sma200_slope >= 0:
            return None

        # Gate 2: fresh MACD cross with a rising (bullish) / falling (bearish) histogram
        age = macd_cross_age(indicators.macd_line, indicators.macd_signal, bullish, _MOMENTUM_LOOKBACK)
        if age is None:
            return None
        hist = indicators.macd_hist
        n = len(hist)
        cross_i = n - 1 - age
        if cross_i < 1:
            return None
        histogram_rising = hist.iloc[-1] > hist.iloc[-2] if bullish else hist.iloc[-1] < hist.iloc[-2]
        if not histogram_rising:
            return None

        # Exit levels: crossover candle's extreme + ATR buffer, fixed 2:1 TP
        # unless the next pivot arrives sooner
        atr = indicators.latest(indicators.atr14)
        crossover_candle = df.iloc[cross_i]
        if bullish:
            stop_loss = float(crossover_candle["low"]) - atr * 0.5
            risk = price - stop_loss
            fixed_target = price + _FIXED_REWARD_RISK * risk
            pivot_target = min((lvl for lvl in indicators.pivots.sorted_levels() if lvl > price), default=fixed_target)
            take_profit = min(fixed_target, pivot_target)
        else:
            stop_loss = float(crossover_candle["high"]) + atr * 0.5
            risk = stop_loss - price
            fixed_target = price - _FIXED_REWARD_RISK * risk
            pivot_target = max((lvl for lvl in indicators.pivots.sorted_levels() if lvl < price), default=fixed_target)
            take_profit = max(fixed_target, pivot_target)

        if risk <= 0:
            return None
        reward = abs(take_profit - price)
        reward_risk = reward / risk
        if reward_risk < _MIN_REWARD_RISK:
            return None

        slope_pct = abs(sma200_slope) / max(price, 1e-9)
        regime_score = 15.0 + min(15.0, slope_pct * 1500.0)

        trigger_score = freshness_score(age, 25.0, _MOMENTUM_LOOKBACK)

        nearest_pivot = indicators.pivots.nearest_level(price)
        pivot_distance_pct = abs(price - nearest_pivot) / price
        location_score = 25.0 * max(0.3, 1.0 - min(1.0, pivot_distance_pct / 0.02))

        trigger_volume = float(df["volume"].iloc[-1])
        avg_volume = indicators.latest(indicators.volume_sma20)
        volume_ratio = (trigger_volume / avg_volume) if avg_volume > 0 else 1.0
        volume_score = min(20.0, max(6.0, 6.0 + (volume_ratio - 1.0) * 14.0))

        cross_word = "bullish" if bullish else "bearish"
        factors = [
            ScoreFactor(f"Regime: SMA200 sloping {'up' if sma200_slope > 0 else 'down'}", round(regime_score, 1), 30.0),
            ScoreFactor(f"Trigger: MACD {cross_word} cross, {age} candle(s) old, histogram expanding", round(trigger_score, 1), 25.0),
            ScoreFactor(f"Location: {pivot_distance_pct * 100:.1f}% from nearest pivot", round(location_score, 1), 25.0),
            ScoreFactor(f"Volume: {volume_ratio:.1f}x 20-period average", round(volume_score, 1), 20.0),
        ]

        return build_signal(df, "swing", self.id, bullish, price, take_profit, stop_loss, reward_risk, factors)
