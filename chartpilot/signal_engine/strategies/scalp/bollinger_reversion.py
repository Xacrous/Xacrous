"""5.2 Bollinger Band Mean-Reversion (+RSI).

Fades short-term extremes rather than chasing momentum. A Bollinger %B
reading near its extremes is just a normalized distance-from-mean (z-score)
trigger, confirmed by a Stochastic %K/%D cross out of its own extreme zone
and by trading near a high-volume node rather than a thin low-volume
air-gap. Deprioritized when EMA9/21 shows a strong directional slope, since
fading a real trend is the main way mean-reversion scalps lose.
"""

from __future__ import annotations

import pandas as pd

from chartpilot.signal_engine.base_strategy import BaseStrategy, Signal
from chartpilot.signal_engine.scorer import ScoreFactor
from chartpilot.signal_engine.strategies.common import build_signal, freshness_score, macd_cross_age
from chartpilot.ta_engine.indicators import IndicatorSet

_STOCH_CROSS_LOOKBACK = 3
_STOCH_OVERSOLD = 20.0
_STOCH_OVERBOUGHT = 80.0
_RSI_OVERSOLD = 30.0
_RSI_OVERBOUGHT = 70.0
_PIERCE_TOLERANCE = 0.05  # %B within this of the band counts as a touch/pierce
_HVN_DISTANCE_GATE_PCT = 0.03
_SWING_LOOKBACK = 5
_MIN_REWARD_RISK = 1.0
_EXPIRY_CANDLES = 12


class BollingerReversionStrategy(BaseStrategy):
    id = "bollinger_mean_reversion"
    display_name = "Bollinger Band Mean-Reversion"
    mode = "scalp"
    required_indicators = ["bb_lower", "bb_mid", "bb_upper", "bb_percent", "rsi14", "stoch_k", "stoch_d", "atr14", "volume_profile"]

    def evaluate(self, df: pd.DataFrame, indicators: IndicatorSet) -> Signal | None:
        long_signal = self._evaluate_direction(df, indicators, bullish=True)
        if long_signal is not None:
            return long_signal
        return self._evaluate_direction(df, indicators, bullish=False)

    def _evaluate_direction(self, df: pd.DataFrame, indicators: IndicatorSet, bullish: bool) -> Signal | None:
        price = float(df["close"].iloc[-1])

        # Gate 1: price touches/pierces the band
        bb_percent = float(indicators.bb_percent.iloc[-1])
        if bullish and bb_percent > _PIERCE_TOLERANCE:
            return None
        if not bullish and bb_percent < 1.0 - _PIERCE_TOLERANCE:
            return None

        # Gate 2: RSI at the extreme and turning
        rsi = indicators.rsi14
        curr_rsi, prev_rsi = float(rsi.iloc[-1]), float(rsi.iloc[-2])
        if bullish and not (curr_rsi < _RSI_OVERSOLD and curr_rsi > prev_rsi):
            return None
        if not bullish and not (curr_rsi > _RSI_OVERBOUGHT and curr_rsi < prev_rsi):
            return None

        # Gate 3: Stochastic %K/%D cross out of its own extreme zone
        stoch_age = macd_cross_age(indicators.stoch_k, indicators.stoch_d, bullish, _STOCH_CROSS_LOOKBACK)
        if stoch_age is None:
            return None
        cross_i = len(indicators.stoch_k) - 1 - stoch_age
        stoch_before_cross = float(indicators.stoch_k.iloc[cross_i - 1])
        if bullish and stoch_before_cross > _STOCH_OVERSOLD:
            return None
        if not bullish and stoch_before_cross < _STOCH_OVERBOUGHT:
            return None

        # Gate 4: not a thin low-volume air-gap
        hvns = indicators.volume_profile.high_volume_nodes(3)
        if not hvns:
            return None
        min_hvn_distance_pct = min(abs(price - node.price_mid) / price for node in hvns)
        if min_hvn_distance_pct > _HVN_DISTANCE_GATE_PCT:
            return None

        # Exit levels
        atr = indicators.latest(indicators.atr14)
        recent = df.iloc[-_SWING_LOOKBACK:]
        take_profit = indicators.latest(indicators.bb_mid)
        if bullish:
            swing_stop = float(recent["low"].min())
            atr_stop = price - 0.5 * atr
            stop_loss = max(swing_stop, atr_stop)  # tighter of the two
        else:
            swing_stop = float(recent["high"].max())
            atr_stop = price + 0.5 * atr
            stop_loss = min(swing_stop, atr_stop)

        risk = abs(price - stop_loss)
        reward = abs(take_profit - price)
        if risk <= 0:
            return None
        reward_risk = reward / risk
        if reward_risk < _MIN_REWARD_RISK:
            return None

        # Momentum alignment: flatter EMA9/21 spread = less fighting a real trend
        ema_spread_pct = abs(indicators.latest(indicators.ema9) - indicators.latest(indicators.ema21)) / price
        momentum_score = 20.0 * max(0.0, 1.0 - min(1.0, ema_spread_pct / 0.01))

        # Trigger strength: how deep the pierce + how strong the RSI turn
        pierce_depth = abs(bb_percent) if bullish else abs(bb_percent - 1.0)
        rsi_turn = abs(curr_rsi - prev_rsi)
        trigger_score = min(30.0, 15.0 * min(1.0, pierce_depth / 0.3) + 15.0 * min(1.0, rsi_turn / 8.0))

        stoch_score = freshness_score(stoch_age, 25.0, _STOCH_CROSS_LOOKBACK)

        volume_score = 25.0 * max(0.0, 1.0 - min(1.0, min_hvn_distance_pct / _HVN_DISTANCE_GATE_PCT))

        band_word = "lower" if bullish else "upper"
        factors = [
            ScoreFactor("Momentum alignment: EMA9/21 flat, not fighting a strong trend", round(momentum_score, 1), 20.0),
            ScoreFactor(f"Trigger strength: pierces {band_word} band, RSI {prev_rsi:.0f}→{curr_rsi:.0f}", round(trigger_score, 1), 30.0),
            ScoreFactor(f"Stochastic confirmation: %K/%D cross, {stoch_age} candle(s) old", round(stoch_score, 1), 25.0),
            ScoreFactor(f"Volume profile context: {min_hvn_distance_pct * 100:.2f}% from nearest HVN", round(volume_score, 1), 25.0),
        ]

        return build_signal(df, "scalp", self.id, bullish, price, take_profit, stop_loss, reward_risk, factors, expiry_candles=_EXPIRY_CANDLES)
