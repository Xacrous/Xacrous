"""5.3 VWAP Reversion / Bounce.

Trades around the session's volume-weighted fair-value anchor. This is a
continuation play, not a reversal play: price must already be trading on
one side of VWAP (the prevailing bias), pull back toward it, and print a
reversal candle at/near the line — entering *with* that bias, not against
it.
"""

from __future__ import annotations

import pandas as pd

from chartpilot.signal_engine.base_strategy import BaseStrategy, Signal
from chartpilot.signal_engine.scorer import ScoreFactor
from chartpilot.signal_engine.strategies.common import build_signal
from chartpilot.ta_engine.indicators import IndicatorSet

_BIAS_LOOKBACK = 15
_BIAS_MAJORITY = 0.7
_VWAP_TOUCH_TOLERANCE_PCT = 0.0015
_FIXED_TARGET_PCT = 0.0035
_MIN_REWARD_RISK = 1.0
_EXPIRY_CANDLES = 12


class VwapReversionStrategy(BaseStrategy):
    id = "vwap_reversion"
    display_name = "VWAP Reversion / Bounce"
    mode = "scalp"
    required_indicators = ["vwap", "rsi14", "atr14"]

    def evaluate(self, df: pd.DataFrame, indicators: IndicatorSet) -> Signal | None:
        long_signal = self._evaluate_direction(df, indicators, bullish=True)
        if long_signal is not None:
            return long_signal
        return self._evaluate_direction(df, indicators, bullish=False)

    def _evaluate_direction(self, df: pd.DataFrame, indicators: IndicatorSet, bullish: bool) -> Signal | None:
        price = float(df["close"].iloc[-1])
        vwap = indicators.vwap
        vwap_now = float(vwap.iloc[-1])

        # Gate 1: prevailing bias — price mostly on one side of VWAP recently
        recent_close = df["close"].iloc[-_BIAS_LOOKBACK:]
        recent_vwap = vwap.iloc[-_BIAS_LOOKBACK:]
        on_side = (recent_close > recent_vwap) if bullish else (recent_close < recent_vwap)
        bias_ratio = float(on_side.mean())
        if bias_ratio < _BIAS_MAJORITY:
            return None

        # Gate 2: pullback to within tolerance of VWAP
        distance_pct = abs(price - vwap_now) / price
        if distance_pct > _VWAP_TOUCH_TOLERANCE_PCT:
            return None

        # Gate 3: reversal candle printed in the direction of the bias
        candle = df.iloc[-1]
        reversal = (candle["close"] > candle["open"]) if bullish else (candle["close"] < candle["open"])
        if not reversal:
            return None

        # Exit levels
        atr = indicators.latest(indicators.atr14)
        if bullish:
            take_profit = price * (1 + _FIXED_TARGET_PCT)
            stop_loss = vwap_now - atr * 0.25
        else:
            take_profit = price * (1 - _FIXED_TARGET_PCT)
            stop_loss = vwap_now + atr * 0.25

        risk = abs(price - stop_loss)
        reward = abs(take_profit - price)
        if risk <= 0:
            return None
        reward_risk = reward / risk
        if reward_risk < _MIN_REWARD_RISK:
            return None

        bias_score = 25.0 * min(1.0, (bias_ratio - _BIAS_MAJORITY) / (1.0 - _BIAS_MAJORITY) + 0.4)
        touch_score = 30.0 * max(0.3, 1.0 - distance_pct / _VWAP_TOUCH_TOLERANCE_PCT)
        body_pct = abs(candle["close"] - candle["open"]) / max(price, 1e-9)
        reversal_score = min(25.0, 10.0 + body_pct * 4000.0)
        volume_score = 20.0

        direction_word = "above" if bullish else "below"
        factors = [
            ScoreFactor(f"Bias: price {direction_word} VWAP {bias_ratio * 100:.0f}% of the last {_BIAS_LOOKBACK} candles", round(bias_score, 1), 25.0),
            ScoreFactor(f"Trigger: pulled back to {distance_pct * 100:.2f}% of VWAP", round(touch_score, 1), 30.0),
            ScoreFactor("Reversal candle confirms rejection at VWAP", round(reversal_score, 1), 25.0),
            ScoreFactor("Volume/location context: fixed target near session anchor", volume_score, 20.0),
        ]

        return build_signal(df, "scalp", self.id, bullish, price, take_profit, stop_loss, reward_risk, factors, expiry_candles=_EXPIRY_CANDLES)
