import numpy as np
import pandas as pd
import pytest

from chartpilot.signal_engine.strategies.swing.trend_following import TrendFollowingStrategy
from chartpilot.ta_engine.indicators import IndicatorSet
from chartpilot.ta_engine.levels import PivotLevels

N = 60


def _base_df(price=145.2):
    close = pd.Series(np.linspace(140, price, N))
    high = close + 0.5
    low = close - 0.5
    open_ = close - 0.1
    volume = pd.Series([100.0] * N)
    volume.iloc[-1] = 160.0
    df = pd.DataFrame({
        "open_time": range(N), "open": open_, "high": high, "low": low, "close": close, "volume": volume,
    })
    df.attrs["symbol"] = "BTCUSDT"
    df.attrs["timeframe"] = "4h"
    return df


def _bullish_indicators(**overrides) -> IndicatorSet:
    defaults = dict(
        mode="swing",
        sma20=pd.Series([143.0] * N),
        sma50=pd.Series([142.0] * N),
        sma200=pd.Series([130.0] * N),
        rsi14=pd.Series([50.0] * N),
        macd_line=pd.Series([-0.1] * (N - 1) + [0.2]),  # bullish cross on last bar
        macd_signal=pd.Series([0.0] * N),
        atr14=pd.Series([1.0] * N),
        volume_sma20=pd.Series([100.0] * N),
        pivots=PivotLevels(pp=145.0, r1=152.0, r2=155.0, r3=158.0, s1=140.0, s2=138.0, s3=136.0),
    )
    defaults["macd_hist"] = defaults["macd_line"] - defaults["macd_signal"]
    defaults.update(overrides)
    return IndicatorSet(**defaults)


def test_all_gates_pass_produces_long_signal():
    df = _base_df()
    ind = _bullish_indicators()
    strat = TrendFollowingStrategy()
    signal = strat.evaluate(df, ind)
    assert signal is not None
    assert signal.direction == "long"
    assert signal.symbol == "BTCUSDT"
    assert signal.timeframe == "4h"
    assert signal.strategy == "trend_following_ma_cross"
    assert 0 <= signal.confidence <= 100
    assert signal.reward_risk_ratio >= 1.5
    assert len(signal.rationale) == 4
    assert signal.entry == pytest.approx(float(df["close"].iloc[-1]))


def test_trend_gate_fails_when_not_stacked():
    df = _base_df()
    ind = _bullish_indicators(sma50=pd.Series([146.0] * N))  # price no longer > sma50
    signal = TrendFollowingStrategy().evaluate(df, ind)
    assert signal is None


def test_momentum_gate_fails_without_trigger():
    df = _base_df()
    # no MACD cross, RSI never dipped below 40 to reclaim
    ind = _bullish_indicators(macd_line=pd.Series([0.5] * N), macd_signal=pd.Series([1.0] * N))
    signal = TrendFollowingStrategy().evaluate(df, ind)
    assert signal is None


def test_location_gate_fails_when_far_from_pivot():
    df = _base_df()
    ind = _bullish_indicators(
        pivots=PivotLevels(pp=200.0, r1=210.0, r2=220.0, r3=230.0, s1=190.0, s2=180.0, s3=170.0)
    )
    signal = TrendFollowingStrategy().evaluate(df, ind)
    assert signal is None


def test_volume_gate_fails_below_average():
    df = _base_df()
    df = df.copy()
    df["volume"] = 50.0  # below the 100.0 average
    ind = _bullish_indicators()
    signal = TrendFollowingStrategy().evaluate(df, ind)
    assert signal is None


def test_reward_risk_below_minimum_is_rejected():
    df = _base_df()
    # TP (next pivot above price) very close to entry -> R:R too low
    ind = _bullish_indicators(
        pivots=PivotLevels(pp=145.0, r1=145.5, r2=146.0, r3=147.0, s1=140.0, s2=138.0, s3=136.0)
    )
    signal = TrendFollowingStrategy().evaluate(df, ind)
    assert signal is None


def test_rsi_reclaim_from_deep_oversold_is_not_a_valid_trigger():
    df = _base_df()
    rsi = pd.Series([50.0] * N)
    rsi.iloc[-6:] = [15.0, 18.0, 20.0, 25.0, 35.0, 41.0]  # reclaim, but originated below 20
    ind = _bullish_indicators(
        macd_line=pd.Series([0.5] * N), macd_signal=pd.Series([1.0] * N),  # no MACD trigger
        rsi14=rsi,
    )
    signal = TrendFollowingStrategy().evaluate(df, ind)
    assert signal is None


def test_rsi_reclaim_from_shallow_pullback_is_a_valid_trigger():
    df = _base_df()
    rsi = pd.Series([50.0] * N)
    rsi.iloc[-6:] = [35.0, 32.0, 30.0, 33.0, 37.0, 41.0]  # shallow pullback, reclaims 40
    ind = _bullish_indicators(
        macd_line=pd.Series([0.5] * N), macd_signal=pd.Series([1.0] * N),  # no MACD trigger
        rsi14=rsi,
    )
    signal = TrendFollowingStrategy().evaluate(df, ind)
    assert signal is not None
    assert any("RSI" in line for line in signal.rationale)
