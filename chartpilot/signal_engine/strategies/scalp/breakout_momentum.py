"""5.5 Breakout / Momentum Scalping — the fast-timeframe breakout play.

Trades the explosive move out of a tight consolidation on a 1-15 minute
window, same volume-confirmation logic as 4.3 but taken and banked fast: a
quick 1.5-2x the risk distance rather than a full measured move, and the
shortest expiry of any scalp strategy (5-10 candles).
"""

from __future__ import annotations

import pandas as pd

from chartpilot.signal_engine.base_strategy import BaseStrategy, Signal
from chartpilot.signal_engine.scorer import ScoreFactor
from chartpilot.signal_engine.strategies.common import build_signal
from chartpilot.ta_engine.indicators import IndicatorSet
from chartpilot.ta_engine.patterns import detect_range_breakout

_CONSOLIDATION_LOOKBACK = 10
_VOLUME_MULTIPLIER = 1.5
_TARGET_MULTIPLE = 1.75  # midpoint of the 1.5-2x risk-distance target
_EXPIRY_CANDLES = 7


class BreakoutMomentumScalpStrategy(BaseStrategy):
    id = "breakout_momentum_scalp"
    display_name = "Breakout / Momentum Scalping"
    mode = "scalp"
    required_indicators = ["atr14", "volume_sma20"]

    def evaluate(self, df: pd.DataFrame, indicators: IndicatorSet) -> Signal | None:
        breakout = detect_range_breakout(df, lookback=_CONSOLIDATION_LOOKBACK, volume_multiplier=_VOLUME_MULTIPLIER)
        if breakout is None:
            return None
        bullish = breakout.direction == "long"
        price = breakout.breakout_price

        atr = indicators.latest(indicators.atr14)
        if bullish:
            stop_loss = breakout.range_high - atr * 0.3
        else:
            stop_loss = breakout.range_low + atr * 0.3

        risk = abs(price - stop_loss)
        if risk <= 0:
            return None
        take_profit = price + _TARGET_MULTIPLE * risk if bullish else price - _TARGET_MULTIPLE * risk
        reward_risk = _TARGET_MULTIPLE

        range_width_pct = breakout.range_height / price if price else 1.0
        regime_score = 30.0 * max(0.3, 1.0 - min(1.0, range_width_pct / 0.03))
        breakout_distance = abs(price - (breakout.range_high if bullish else breakout.range_low))
        trigger_score = 10.0 + min(15.0, (breakout_distance / atr) * 10.0) if atr > 0 else 15.0
        location_score = 20.0
        volume_score = min(20.0, 10.0 + (breakout.volume_ratio - _VOLUME_MULTIPLIER) * 10.0)

        direction_word = "above" if bullish else "below"
        factors = [
            ScoreFactor(f"Regime: {range_width_pct * 100:.2f}%-wide tight consolidation", round(regime_score, 1), 30.0),
            ScoreFactor(f"Trigger: closed {direction_word} the consolidation decisively", round(trigger_score, 1), 25.0),
            ScoreFactor(f"Location: range {breakout.range_low:g}-{breakout.range_high:g}", location_score, 25.0),
            ScoreFactor(f"Volume: {breakout.volume_ratio:.1f}x recent average", round(volume_score, 1), 20.0),
        ]

        return build_signal(df, "scalp", self.id, bullish, price, take_profit, stop_loss, reward_risk, factors, expiry_candles=_EXPIRY_CANDLES)
