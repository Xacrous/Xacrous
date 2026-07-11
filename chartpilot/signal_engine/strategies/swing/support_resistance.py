"""4.4 Support/Resistance Reversal — the counter-trend counterpart to 4.3.

For markets that are ranging rather than trending: price must reach a level
that's held 2-3+ times historically, RSI must be at the corresponding
extreme, and a reversal (engulfing) candle must confirm rejection. Actively
discouraged when the SMA50/SMA200 spread shows a strong directional bias,
since range strategies fight trends by design.
"""

from __future__ import annotations

import pandas as pd

from chartpilot.signal_engine.base_strategy import BaseStrategy, Signal
from chartpilot.signal_engine.scorer import ScoreFactor
from chartpilot.signal_engine.strategies.common import build_signal, is_bearish_engulfing, is_bullish_engulfing
from chartpilot.ta_engine.indicators import IndicatorSet
from chartpilot.ta_engine.levels import fractal_swing_points

_LEVEL_TOLERANCE_PCT = 0.01
_MIN_TOUCHES = 2
_RSI_OVERSOLD = 30.0
_RSI_OVERBOUGHT = 70.0
_MIN_REWARD_RISK = 1.5
_STRONG_TREND_SPREAD_PCT = 0.05  # |sma50-sma200|/sma200 above this counts as a "strong" trend


def _cluster_levels(prices: list[float], tolerance_pct: float) -> list[tuple[float, int]]:
    clusters: list[list[float]] = []
    for p in sorted(prices):
        placed = False
        for cluster in clusters:
            if abs(p - cluster[-1]) / max(p, 1e-9) <= tolerance_pct:
                cluster.append(p)
                placed = True
                break
        if not placed:
            clusters.append([p])
    return [(sum(c) / len(c), len(c)) for c in clusters]


def _find_tested_level(prices: list[float], current_price: float, tolerance_pct: float, min_touches: int) -> tuple[float, int] | None:
    for level, touches in _cluster_levels(prices, tolerance_pct):
        if touches >= min_touches and abs(current_price - level) / max(current_price, 1e-9) <= tolerance_pct:
            return level, touches
    return None


class SupportResistanceReversalStrategy(BaseStrategy):
    id = "support_resistance_reversal"
    display_name = "Support/Resistance Reversal"
    mode = "swing"
    required_indicators = ["sma50", "sma200", "rsi14", "atr14", "volume_sma20"]

    def evaluate(self, df: pd.DataFrame, indicators: IndicatorSet) -> Signal | None:
        long_signal = self._evaluate_direction(df, indicators, bullish=True)
        if long_signal is not None:
            return long_signal
        return self._evaluate_direction(df, indicators, bullish=False)

    def _evaluate_direction(self, df: pd.DataFrame, indicators: IndicatorSet, bullish: bool) -> Signal | None:
        price = float(df["close"].iloc[-1])
        swing_highs, swing_lows = fractal_swing_points(df, order=2)

        # Gate 1: price at a level tested 2-3+ times historically
        if bullish:
            prices = [float(df["low"].iloc[i]) for i in swing_lows]
        else:
            prices = [float(df["high"].iloc[i]) for i in swing_highs]
        level_hit = _find_tested_level(prices, price, _LEVEL_TOLERANCE_PCT, _MIN_TOUCHES)
        if level_hit is None:
            return None
        level, touches = level_hit

        # Gate 2: RSI at the corresponding extreme
        rsi = float(indicators.rsi14.iloc[-1])
        if bullish and rsi >= _RSI_OVERSOLD:
            return None
        if not bullish and rsi <= _RSI_OVERBOUGHT:
            return None

        # Gate 3: reversal candle confirming rejection
        reversal = is_bullish_engulfing(df) if bullish else is_bearish_engulfing(df)
        if not reversal:
            return None

        # Exit levels
        atr = indicators.latest(indicators.atr14)
        trigger = df.iloc[-1]
        if bullish:
            stop_loss = float(trigger["low"]) - atr * 0.25
            opposite = [float(df["high"].iloc[i]) for i in swing_highs if float(df["high"].iloc[i]) > price]
            take_profit = min(opposite) if opposite else price + 2 * atr
        else:
            stop_loss = float(trigger["high"]) + atr * 0.25
            opposite = [float(df["low"].iloc[i]) for i in swing_lows if float(df["low"].iloc[i]) < price]
            take_profit = max(opposite) if opposite else price - 2 * atr

        risk = abs(price - stop_loss)
        reward = abs(take_profit - price)
        if risk <= 0:
            return None
        reward_risk = reward / risk
        if reward_risk < _MIN_REWARD_RISK:
            return None

        # Regime: penalize heavily when a strong directional trend is present —
        # this strategy fights trends by design and should be discouraged then.
        sma50, sma200 = indicators.latest(indicators.sma50), indicators.latest(indicators.sma200)
        trend_spread_pct = abs(sma50 - sma200) / max(sma200, 1e-9)
        regime_score = 30.0 * max(0.15, 1.0 - min(1.0, trend_spread_pct / _STRONG_TREND_SPREAD_PCT))

        trigger_score = 15.0 + min(10.0, (abs(50.0 - rsi) - 20.0) * 0.5) if bullish else 15.0 + min(10.0, (abs(rsi - 50.0) - 20.0) * 0.5)
        trigger_score = max(15.0, min(25.0, trigger_score))

        location_score = min(25.0, 15.0 + (touches - _MIN_TOUCHES) * 3.0)

        trigger_volume = float(df["volume"].iloc[-1])
        avg_volume = indicators.latest(indicators.volume_sma20)
        volume_ratio = (trigger_volume / avg_volume) if avg_volume > 0 else 1.0
        volume_score = min(20.0, max(6.0, 6.0 + (volume_ratio - 1.0) * 14.0))

        level_word = "support" if bullish else "resistance"
        factors = [
            ScoreFactor(f"Regime: ranging, trend spread {trend_spread_pct * 100:.1f}%", round(regime_score, 1), 30.0),
            ScoreFactor(f"Trigger: RSI(14) {rsi:.0f} + reversal candle at {level_word}", round(trigger_score, 1), 25.0),
            ScoreFactor(f"Location: {level_word} {level:g} tested {touches}x historically", round(location_score, 1), 25.0),
            ScoreFactor(f"Volume: {volume_ratio:.1f}x 20-period average", round(volume_score, 1), 20.0),
        ]

        return build_signal(df, "swing", self.id, bullish, price, take_profit, stop_loss, reward_risk, factors)
