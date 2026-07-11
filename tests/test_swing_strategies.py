import numpy as np
import pandas as pd
import pytest

from chartpilot.signal_engine.strategies.swing.breakout import BreakoutStrategy
from chartpilot.signal_engine.strategies.swing.macd_momentum import MacdMomentumStrategy
from chartpilot.signal_engine.strategies.swing.pullback_fib import PullbackFibStrategy
from chartpilot.signal_engine.strategies.swing.support_resistance import SupportResistanceReversalStrategy
from chartpilot.ta_engine.indicators import IndicatorSet
from chartpilot.ta_engine.levels import PivotLevels


def _attrs(df, symbol="TEST", timeframe="4h"):
    df.attrs["symbol"] = symbol
    df.attrs["timeframe"] = timeframe
    return df


# ---------------------------------------------------------------- Pullback / Fib

def _pullback_fixture(pullback_close=120.0):
    n = 90
    lows = np.full(n, 130.0)
    highs = np.full(n, 131.0)
    close = np.full(n, 130.0)
    lows[20], highs[20] = 100.0, 101.0
    lows[50], highs[50] = 140.0, 141.0
    for i in range(20, 51):
        t = (i - 20) / 30
        v = 100 + t * 40
        lows[i], highs[i], close[i] = v - 0.5, v + 0.5, v
    for i in range(51, n):
        close[i] = 130.0
    close[-1] = pullback_close
    lows[-1], highs[-1] = pullback_close - 0.5, pullback_close + 0.5
    volume = np.full(n, 100.0)
    volume[-1] = 150.0
    df = _attrs(pd.DataFrame({
        "open_time": np.arange(n) * 3600_000, "open": close - 0.1, "high": highs, "low": lows,
        "close": close, "volume": volume,
    }))
    atr14 = pd.Series([1.0] * n)
    sma50 = pd.Series(np.linspace(110, 125, n))
    rsi14 = pd.Series([50.0] * n)
    rsi14.iloc[-3:] = [35, 38, 44]
    volume_sma20 = pd.Series([100.0] * n)
    ind = IndicatorSet(mode="swing", rsi14=rsi14, atr14=atr14, volume_sma20=volume_sma20, sma50=sma50)
    return df, ind


def test_pullback_fib_qualifying_setup_produces_signal():
    df, ind = _pullback_fixture()
    signal = PullbackFibStrategy().evaluate(df, ind)
    assert signal is not None
    assert signal.direction == "long"
    assert signal.strategy == "pullback_fibonacci"
    assert signal.reward_risk_ratio >= 1.5


def test_pullback_fib_none_outside_golden_zone():
    df, ind = _pullback_fixture(pullback_close=129.0)  # barely retraced, not in the 38-62% zone
    assert PullbackFibStrategy().evaluate(df, ind) is None


def test_pullback_fib_none_against_sma50_slope():
    df, ind = _pullback_fixture()
    ind.sma50 = pd.Series(np.linspace(125, 110, len(df)))  # sloping down now
    assert PullbackFibStrategy().evaluate(df, ind) is None


# ---------------------------------------------------------------- Breakout

def _breakout_fixture(trigger_close=103.8):
    close = pd.Series([100.0] * 20 + [100, 100.5, 101, 102, trigger_close])
    high, low = close + 0.3, close - 0.3
    volume = pd.Series([50.0] * 24 + [120.0])
    df = _attrs(pd.DataFrame({
        "open_time": np.arange(25) * 3600_000, "open": close - 0.1, "high": high, "low": low,
        "close": close, "volume": volume,
    }))
    ind = IndicatorSet(mode="swing", rsi14=pd.Series([50.0] * 25), atr14=pd.Series([0.3] * 25),
                        volume_sma20=pd.Series([50.0] * 25))
    return df, ind


def test_breakout_qualifying_setup_produces_signal():
    df, ind = _breakout_fixture()
    signal = BreakoutStrategy().evaluate(df, ind)
    assert signal is not None
    assert signal.direction == "long"
    assert signal.strategy == "breakout"


def test_breakout_none_without_range_break():
    df, ind = _breakout_fixture(trigger_close=100.1)  # stays inside the range
    assert BreakoutStrategy().evaluate(df, ind) is None


# ---------------------------------------------------------------- Support/Resistance Reversal

def _sr_reversal_fixture():
    n = 60
    close = np.full(n, 0.68)
    for idx in (10, 30):
        close[idx - 2:idx + 3] = [0.66, 0.64, 0.620, 0.64, 0.66]
    close[55:58] = [0.66, 0.64, 0.618]
    close[58], close[59] = 0.613, 0.620
    high, low = close + 0.005, close - 0.005
    open_ = close.copy()
    open_[58], open_[59] = 0.618, 0.610
    volume = np.full(n, 100.0)
    df = _attrs(pd.DataFrame({
        "open_time": np.arange(n) * 86_400_000, "open": open_, "high": high, "low": low,
        "close": close, "volume": volume,
    }))
    rsi14 = pd.Series([50.0] * n)
    rsi14.iloc[-1] = 25.0
    ind = IndicatorSet(mode="swing", rsi14=rsi14, atr14=pd.Series([0.005] * n),
                        volume_sma20=pd.Series([100.0] * n), sma50=pd.Series([0.65] * n),
                        sma200=pd.Series([0.64] * n))
    return df, ind


def test_support_resistance_reversal_qualifying_setup():
    df, ind = _sr_reversal_fixture()
    signal = SupportResistanceReversalStrategy().evaluate(df, ind)
    assert signal is not None
    assert signal.direction == "long"


def test_support_resistance_reversal_none_without_oversold_rsi():
    df, ind = _sr_reversal_fixture()
    ind.rsi14.iloc[-1] = 55.0  # not oversold
    assert SupportResistanceReversalStrategy().evaluate(df, ind) is None


def test_support_resistance_reversal_none_when_level_untested():
    df, ind = _sr_reversal_fixture()
    # collapse the earlier touches so the level was only hit once
    df.loc[8:12, "low"] = 0.66
    df.loc[28:32, "low"] = 0.66
    assert SupportResistanceReversalStrategy().evaluate(df, ind) is None


# ---------------------------------------------------------------- MACD Momentum

def _macd_momentum_fixture():
    n = 40
    close = pd.Series(np.linspace(13.5, 13.85, n))
    high, low, open_ = close + 0.05, close - 0.05, close - 0.02
    volume = pd.Series([100.0] * n)
    volume.iloc[-1] = 130.0
    df = _attrs(pd.DataFrame({
        "open_time": np.arange(n) * 3600_000 * 4, "open": open_, "high": high, "low": low,
        "close": close, "volume": volume,
    }), symbol="LINKUSDT")

    sma200 = pd.Series(np.linspace(13.0, 13.3, n))
    macd_line = pd.Series([-0.02] * (n - 1) + [0.03])
    macd_signal = pd.Series([0.0] * n)
    macd_hist = macd_line - macd_signal
    pivots = PivotLevels(pp=14.3, r1=14.6, r2=14.9, r3=15.2, s1=13.2, s2=13.0, s3=12.8)
    ind = IndicatorSet(mode="swing", rsi14=pd.Series([50.0] * n), atr14=pd.Series([0.05] * n),
                        volume_sma20=pd.Series([100.0] * n), sma200=sma200, macd_line=macd_line,
                        macd_signal=macd_signal, macd_hist=macd_hist, pivots=pivots)
    return df, ind


def test_macd_momentum_qualifying_setup():
    df, ind = _macd_momentum_fixture()
    signal = MacdMomentumStrategy().evaluate(df, ind)
    assert signal is not None
    assert signal.direction == "long"
    assert signal.reward_risk_ratio == pytest.approx(2.0, abs=0.2)


def test_macd_momentum_none_against_sma200_slope():
    df, ind = _macd_momentum_fixture()
    ind.sma200 = pd.Series(np.linspace(13.3, 13.0, len(df)))  # sloping down now
    assert MacdMomentumStrategy().evaluate(df, ind) is None
