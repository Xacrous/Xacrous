"""VWAP Crossover — the Trade tab's spot-only long entry.

Buy when price crosses above VWAP, i.e. the market is showing strength
relative to the volume-weighted average price the day's participants have
actually traded at. Spot-only means there's no short side to this: without
margin/borrowing, a spot account can't profit from price falling, so unlike
the bidirectional swing/scalp strategies this one only ever looks for longs.

Exit levels follow the ATR-based approach: a stop this far below entry
adapts to the current volatility regime, rather than a fixed-% stop that's
too tight in a hot market or too loose in a quiet one. The target is a
multiple of that same risk distance (R) — 1.75R, the midpoint of the
1.5-2R range that historically holds up best for VWAP reversion-adjacent
setups, versus reaching for a bigger multiple that needs price to travel
further before a chop takes it back. Resolution (which of TP/SL "wins" when
a single candle touches both) is handled by `resolution.resolve_signal`,
which already applies the conservative stop-wins-ties default.
"""

from __future__ import annotations

import pandas as pd

from chartpilot.signal_engine.base_strategy import BaseStrategy, Signal
from chartpilot.signal_engine.scorer import ScoreFactor
from chartpilot.signal_engine.strategies.common import build_signal, freshness_score, macd_cross_age
from chartpilot.ta_engine.indicators import IndicatorSet

_CROSS_LOOKBACK = 3
_ATR_STOP_MULTIPLIER = 1.75  # midpoint of the recommended 1.5-2x ATR stop distance
_TARGET_R_MULTIPLE = 1.75  # midpoint of the recommended 1.5-2R target
_MAX_EXTENSION_ATR = 3.0  # don't chase a move already this far past VWAP
_EXPIRY_CANDLES = 15


class VwapCrossoverStrategy(BaseStrategy):
    id = "vwap_crossover"
    display_name = "VWAP Crossover"
    mode = "trade"
    required_indicators = ["vwap", "atr14", "volume_sma20"]

    def evaluate(self, df: pd.DataFrame, indicators: IndicatorSet) -> Signal | None:
        price = float(df["close"].iloc[-1])
        vwap = indicators.vwap
        vwap_now = float(vwap.iloc[-1])

        age = macd_cross_age(df["close"], vwap, bullish=True, lookback=_CROSS_LOOKBACK)
        if age is None:
            return None

        atr = indicators.latest(indicators.atr14)
        if atr <= 0:
            return None

        extension_atr = (price - vwap_now) / atr
        if extension_atr > _MAX_EXTENSION_ATR:
            return None  # already run too far past VWAP to enter fresh

        stop_loss = price - atr * _ATR_STOP_MULTIPLIER
        risk = price - stop_loss
        if risk <= 0:
            return None
        take_profit = price + _TARGET_R_MULTIPLE * risk
        reward_risk = _TARGET_R_MULTIPLE

        momentum_score = freshness_score(age, 30.0, _CROSS_LOOKBACK)

        volume = df["volume"]
        avg_volume = indicators.latest(indicators.volume_sma20)
        volume_ratio = (float(volume.iloc[-1]) / avg_volume) if avg_volume > 0 else 1.0
        volume_score = max(10.0, min(25.0, 10.0 + (volume_ratio - 1.0) * 20.0))

        location_score = 25.0 * max(0.0, 1.0 - extension_atr / _MAX_EXTENSION_ATR)
        trigger_score = 20.0

        factors = [
            ScoreFactor(f"Momentum alignment: price crossed above VWAP {age} candle(s) ago", round(momentum_score, 1), 30.0),
            ScoreFactor(f"Volume: {volume_ratio:.1f}x 20-period average", round(volume_score, 1), 25.0),
            ScoreFactor(f"Location: {extension_atr:.2f} ATR above VWAP, not overextended", round(location_score, 1), 25.0),
            ScoreFactor(f"Trigger strength: ATR-based stop ({_ATR_STOP_MULTIPLIER}x), {_TARGET_R_MULTIPLE}R target", trigger_score, 20.0),
        ]

        return build_signal(df, "trade", self.id, True, price, take_profit, stop_loss, reward_risk, factors, expiry_candles=_EXPIRY_CANDLES)
