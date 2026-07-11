"""4.2 Pullback / Fibonacci Retracement.

Buys the dip inside an established trend rather than chasing new highs: an
impulsive swing must be detected first (ATR-normalized move), then price
must be sitting in the 38.2-61.8% "golden zone" of that swing while the
broader SMA50 trend stays intact and RSI turns up from the 40-50 zone.
"""

from __future__ import annotations

import pandas as pd

from chartpilot.signal_engine.base_strategy import BaseStrategy, Signal
from chartpilot.signal_engine.scorer import ScoreFactor
from chartpilot.signal_engine.strategies.common import build_signal, slope
from chartpilot.ta_engine.indicators import IndicatorSet
from chartpilot.ta_engine.patterns import detect_fibonacci_retracement

_SWING_LOOKBACK = 80
_MIN_ATR_MULTIPLE = 3.0
_SMA_SLOPE_LOOKBACK = 10
_RSI_ZONE_LOW = 40.0
_RSI_ZONE_HIGH = 50.0
_MIN_REWARD_RISK = 1.5


class PullbackFibStrategy(BaseStrategy):
    id = "pullback_fibonacci"
    display_name = "Pullback / Fibonacci Retracement"
    mode = "swing"
    required_indicators = ["sma50", "rsi14", "atr14", "volume_sma20"]

    def evaluate(self, df: pd.DataFrame, indicators: IndicatorSet) -> Signal | None:
        long_signal = self._evaluate_direction(df, indicators, bullish=True)
        if long_signal is not None:
            return long_signal
        return self._evaluate_direction(df, indicators, bullish=False)

    def _evaluate_direction(self, df: pd.DataFrame, indicators: IndicatorSet, bullish: bool) -> Signal | None:
        price = float(df["close"].iloc[-1])

        # Gate 1: an established impulsive swing in the right direction
        fib = detect_fibonacci_retracement(df, indicators.atr14, lookback=min(_SWING_LOOKBACK, len(df)), min_atr_multiple=_MIN_ATR_MULTIPLE)
        if fib is None or fib.direction != ("long" if bullish else "short"):
            return None

        # Gate 2: broader trend (SMA50 slope) intact
        sma50_slope = slope(indicators.sma50, _SMA_SLOPE_LOOKBACK)
        if bullish and sma50_slope <= 0:
            return None
        if not bullish and sma50_slope >= 0:
            return None

        # Gate 3: price inside the golden zone
        zone_low, zone_high = fib.zone()
        if not (zone_low <= price <= zone_high):
            return None

        # Gate 4: RSI turning up (long) / down (short) from the 40-50 zone
        rsi = indicators.rsi14
        curr_rsi, prev_rsi = float(rsi.iloc[-1]), float(rsi.iloc[-2])
        if bullish:
            in_zone = _RSI_ZONE_LOW <= curr_rsi <= _RSI_ZONE_HIGH
            turning = curr_rsi > prev_rsi
        else:
            in_zone = (100 - _RSI_ZONE_HIGH) <= curr_rsi <= (100 - _RSI_ZONE_LOW)
            turning = curr_rsi < prev_rsi
        if not (in_zone and turning):
            return None

        # Exit levels
        atr = indicators.latest(indicators.atr14)
        if bullish:
            take_profit = fib.swing_end
            stop_loss = fib.levels[0.786] - atr * 0.25
        else:
            take_profit = fib.swing_end
            stop_loss = fib.levels[0.786] + atr * 0.25

        risk = abs(price - stop_loss)
        reward = abs(take_profit - price)
        if risk <= 0:
            return None
        reward_risk = reward / risk
        if reward_risk < _MIN_REWARD_RISK:
            return None

        # Scoring
        slope_pct = abs(sma50_slope) / max(price, 1e-9)
        trend_score = 15.0 + min(15.0, slope_pct * 1000.0)
        zone_mid = (zone_low + zone_high) / 2
        zone_half_width = (zone_high - zone_low) / 2 or 1e-9
        centering = 1.0 - min(1.0, abs(price - zone_mid) / zone_half_width)
        location_score = 15.0 + 10.0 * centering
        rsi_distance_from_zone_edge = min(abs(curr_rsi - prev_rsi), 10.0)
        trigger_score = 15.0 + rsi_distance_from_zone_edge
        trigger_score = min(25.0, trigger_score)

        trigger_volume = float(df["volume"].iloc[-1])
        avg_volume = indicators.latest(indicators.volume_sma20)
        volume_ratio = (trigger_volume / avg_volume) if avg_volume > 0 else 1.0
        volume_score = min(20.0, max(8.0, 8.0 + (volume_ratio - 1.0) * 20.0))

        direction_word = "up" if bullish else "down"
        factors = [
            ScoreFactor(f"Trend: SMA50 sloping {'up' if sma50_slope > 0 else 'down'}, broader trend intact", round(trend_score, 1), 30.0),
            ScoreFactor(f"Trigger: RSI(14) turning {direction_word} from the {_RSI_ZONE_LOW:.0f}-{_RSI_ZONE_HIGH:.0f} zone", round(trigger_score, 1), 25.0),
            ScoreFactor("Location: price inside the 38.2-61.8% Fibonacci golden zone", round(location_score, 1), 25.0),
            ScoreFactor(f"Volume: {volume_ratio:.1f}x 20-period average", round(volume_score, 1), 20.0),
        ]

        return build_signal(df, "swing", self.id, bullish, price, take_profit, stop_loss, reward_risk, factors)
