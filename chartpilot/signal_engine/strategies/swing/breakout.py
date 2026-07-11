"""4.3 Breakout Trading.

Trades the resolution of a consolidation range rather than an established
trend: a close beyond a multi-candle range on volume >= 1.5x the 20-period
average is what separates a genuine breakout from a fakeout.
"""

from __future__ import annotations

import pandas as pd

from chartpilot.signal_engine.base_strategy import BaseStrategy, Signal
from chartpilot.signal_engine.scorer import ScoreFactor
from chartpilot.signal_engine.strategies.common import build_signal
from chartpilot.ta_engine.indicators import IndicatorSet
from chartpilot.ta_engine.patterns import detect_range_breakout

_CONSOLIDATION_LOOKBACK = 20
_VOLUME_MULTIPLIER = 1.5
_MIN_REWARD_RISK = 1.5


class BreakoutStrategy(BaseStrategy):
    id = "breakout"
    display_name = "Breakout Trading"
    mode = "swing"
    required_indicators = ["atr14", "volume_sma20"]

    def evaluate(self, df: pd.DataFrame, indicators: IndicatorSet) -> Signal | None:
        breakout = detect_range_breakout(df, lookback=_CONSOLIDATION_LOOKBACK, volume_multiplier=_VOLUME_MULTIPLIER)
        if breakout is None:
            return None
        bullish = breakout.direction == "long"
        price = breakout.breakout_price

        atr = indicators.latest(indicators.atr14)
        if bullish:
            stop_loss = breakout.range_high - atr * 0.5
        else:
            stop_loss = breakout.range_low + atr * 0.5
        take_profit = breakout.measured_move_target()

        risk = abs(price - stop_loss)
        reward = abs(take_profit - price)
        if risk <= 0:
            return None
        reward_risk = reward / risk
        if reward_risk < _MIN_REWARD_RISK:
            return None

        # Regime: how tight the pre-breakout consolidation was (tighter = higher quality)
        range_width_pct = breakout.range_height / price if price else 1.0
        regime_score = 30.0 * max(0.3, 1.0 - min(1.0, range_width_pct / 0.05))

        # Trigger: how decisively price closed beyond the level, ATR-normalized
        breakout_distance = abs(price - (breakout.range_high if bullish else breakout.range_low))
        trigger_score = 10.0 + min(15.0, (breakout_distance / atr) * 10.0) if atr > 0 else 15.0

        # Location: this strategy's entire premise *is* the range boundary
        location_score = 20.0

        volume_score = min(20.0, 10.0 + (breakout.volume_ratio - _VOLUME_MULTIPLIER) * 10.0)

        direction_word = "above" if bullish else "below"
        factors = [
            ScoreFactor(f"Regime: {range_width_pct * 100:.1f}%-wide consolidation resolving", round(regime_score, 1), 30.0),
            ScoreFactor(f"Trigger: closed {direction_word} the range on a decisive candle", round(trigger_score, 1), 25.0),
            ScoreFactor(f"Location: range {breakout.range_low:g}-{breakout.range_high:g}, breakout at {price:g}", round(location_score, 1), 25.0),
            ScoreFactor(f"Volume: {breakout.volume_ratio:.1f}x 20-period average", round(volume_score, 1), 20.0),
        ]

        return build_signal(df, "swing", self.id, bullish, price, take_profit, stop_loss, reward_risk, factors)
