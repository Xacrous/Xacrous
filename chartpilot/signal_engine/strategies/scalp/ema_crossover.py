"""5.1 EMA Crossover Momentum — the default, fastest scalp trigger.

Two EMAs, nothing else required: EMA9 crosses above EMA21 on rising volume.
Crypto's constant small trend-flips generate frequent signals, which is
both the appeal and the risk (overtrading) — the 10-15 candle expiry keeps
stale setups from lingering on the panel.
"""

from __future__ import annotations

import pandas as pd

from chartpilot.signal_engine.base_strategy import BaseStrategy, Signal
from chartpilot.signal_engine.scorer import ScoreFactor
from chartpilot.signal_engine.strategies.common import build_signal, freshness_score, macd_cross_age
from chartpilot.ta_engine.indicators import IndicatorSet

_CROSS_LOOKBACK = 3
_SWING_LOOKBACK = 5
_FIXED_REWARD_RISK = 1.25
_MIN_REWARD_RISK = 1.0
_EXPIRY_CANDLES = 12


class EmaCrossoverStrategy(BaseStrategy):
    id = "ema_crossover_momentum"
    display_name = "EMA Crossover Momentum"
    mode = "scalp"
    required_indicators = ["ema9", "ema21", "volume_sma20", "atr14"]

    def evaluate(self, df: pd.DataFrame, indicators: IndicatorSet) -> Signal | None:
        long_signal = self._evaluate_direction(df, indicators, bullish=True)
        if long_signal is not None:
            return long_signal
        return self._evaluate_direction(df, indicators, bullish=False)

    def _evaluate_direction(self, df: pd.DataFrame, indicators: IndicatorSet, bullish: bool) -> Signal | None:
        price = float(df["close"].iloc[-1])

        age = macd_cross_age(indicators.ema9, indicators.ema21, bullish, _CROSS_LOOKBACK)
        if age is None:
            return None

        volume = df["volume"]
        if float(volume.iloc[-1]) <= float(volume.iloc[-2]):
            return None

        recent = df.iloc[-_SWING_LOOKBACK:]
        if bullish:
            stop_loss = float(recent["low"].min())
        else:
            stop_loss = float(recent["high"].max())

        risk = abs(price - stop_loss)
        if risk <= 0:
            return None
        take_profit = price + _FIXED_REWARD_RISK * risk if bullish else price - _FIXED_REWARD_RISK * risk
        reward_risk = _FIXED_REWARD_RISK
        if reward_risk < _MIN_REWARD_RISK:
            return None

        momentum_score = freshness_score(age, 30.0, _CROSS_LOOKBACK)
        trigger_score = 25.0

        avg_volume = indicators.latest(indicators.volume_sma20)
        volume_ratio = (float(volume.iloc[-1]) / avg_volume) if avg_volume > 0 else 1.0
        volume_score = min(25.0, 10.0 + (volume_ratio - 1.0) * 20.0)
        volume_score = max(10.0, volume_score)

        location_score = 20.0

        cross_word = "above" if bullish else "below"
        factors = [
            ScoreFactor(f"Momentum alignment: EMA9 crossed {cross_word} EMA21, {age} candle(s) ago", round(momentum_score, 1), 30.0),
            ScoreFactor("Trigger strength: rising volume on the cross candle", trigger_score, 25.0),
            ScoreFactor(f"Volume/location context: SL at {_SWING_LOOKBACK}-candle swing extreme", location_score, 20.0),
            ScoreFactor(f"Volume: {volume_ratio:.1f}x 20-period average", round(volume_score, 1), 25.0),
        ]

        return build_signal(df, "scalp", self.id, bullish, price, take_profit, stop_loss, reward_risk, factors, expiry_candles=_EXPIRY_CANDLES)
