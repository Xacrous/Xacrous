"""5.4 Range / Support-Resistance Scalping — the scalp-timeframe sibling of 4.4.

Buy support, sell resistance, repeat, while a well-defined micro-range
holds. Logically incompatible with a genuine breakout (5.5), so it's
suppressed the moment one fires in the same window.
"""

from __future__ import annotations

import pandas as pd

from chartpilot.signal_engine.base_strategy import BaseStrategy, Signal
from chartpilot.signal_engine.scorer import ScoreFactor
from chartpilot.signal_engine.strategies.common import build_signal
from chartpilot.ta_engine.indicators import IndicatorSet
from chartpilot.ta_engine.patterns import detect_range_breakout

_RANGE_LOOKBACK = 20
_MAX_RANGE_WIDTH_PCT = 0.015
_EDGE_ZONE_PCT = 0.25  # price must be within this fraction of the range from an edge
_RSI_OVERSOLD = 40.0
_RSI_OVERBOUGHT = 60.0
_MIN_REWARD_RISK = 1.5
_EXPIRY_CANDLES = 12


class RangeScalpStrategy(BaseStrategy):
    id = "range_scalp"
    display_name = "Range / Support-Resistance Scalping"
    mode = "scalp"
    required_indicators = ["rsi14", "atr14"]

    def evaluate(self, df: pd.DataFrame, indicators: IndicatorSet) -> Signal | None:
        # Suppressed the moment a genuine breakout fires — the two strategies
        # are logically incompatible in the same window.
        if detect_range_breakout(df, lookback=_RANGE_LOOKBACK, volume_multiplier=1.5) is not None:
            return None

        long_signal = self._evaluate_direction(df, indicators, bullish=True)
        if long_signal is not None:
            return long_signal
        return self._evaluate_direction(df, indicators, bullish=False)

    def _evaluate_direction(self, df: pd.DataFrame, indicators: IndicatorSet, bullish: bool) -> Signal | None:
        price = float(df["close"].iloc[-1])
        window = df.iloc[-_RANGE_LOOKBACK:]
        range_high, range_low = float(window["high"].max()), float(window["low"].min())
        range_width = range_high - range_low
        if range_width <= 0:
            return None
        width_pct = range_width / price
        if width_pct > _MAX_RANGE_WIDTH_PCT:
            return None

        position_in_range = (price - range_low) / range_width  # 0 = floor, 1 = ceiling
        if bullish and position_in_range > _EDGE_ZONE_PCT:
            return None
        if not bullish and position_in_range < 1.0 - _EDGE_ZONE_PCT:
            return None

        rsi = indicators.rsi14
        curr_rsi, prev_rsi = float(rsi.iloc[-1]), float(rsi.iloc[-2])
        if bullish and not (curr_rsi < _RSI_OVERSOLD and curr_rsi > prev_rsi):
            return None
        if not bullish and not (curr_rsi > _RSI_OVERBOUGHT and curr_rsi < prev_rsi):
            return None

        atr = indicators.latest(indicators.atr14)
        if bullish:
            take_profit = range_high
            stop_loss = range_low - atr * 0.3
        else:
            take_profit = range_low
            stop_loss = range_high + atr * 0.3

        risk = abs(price - stop_loss)
        reward = abs(take_profit - price)
        if risk <= 0:
            return None
        reward_risk = reward / risk
        if reward_risk < _MIN_REWARD_RISK:
            return None

        regime_score = 30.0 * max(0.3, 1.0 - width_pct / _MAX_RANGE_WIDTH_PCT)
        edge_closeness = position_in_range if bullish else (1.0 - position_in_range)
        trigger_score = 25.0 * max(0.3, 1.0 - edge_closeness / _EDGE_ZONE_PCT)
        rsi_turn = abs(curr_rsi - prev_rsi)
        location_score = min(25.0, 12.0 + rsi_turn * 1.5)
        volume_score = 20.0

        edge_word = "floor" if bullish else "ceiling"
        factors = [
            ScoreFactor(f"Regime: {width_pct * 100:.2f}%-wide micro-range holding", round(regime_score, 1), 30.0),
            ScoreFactor(f"Trigger: at the range {edge_word}", round(trigger_score, 1), 25.0),
            ScoreFactor(f"Location: RSI(14) {prev_rsi:.0f}→{curr_rsi:.0f} turning", round(location_score, 1), 25.0),
            ScoreFactor("Volume/location context: range-bound conditions", volume_score, 20.0),
        ]

        return build_signal(df, "scalp", self.id, bullish, price, take_profit, stop_loss, reward_risk, factors, expiry_candles=_EXPIRY_CANDLES)
