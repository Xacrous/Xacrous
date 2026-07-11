import numpy as np
import pandas as pd
import pytest

from chartpilot.signal_engine.strategies.scalp.bollinger_reversion import BollingerReversionStrategy
from chartpilot.signal_engine.strategies.scalp.breakout_momentum import BreakoutMomentumScalpStrategy
from chartpilot.signal_engine.strategies.scalp.ema_crossover import EmaCrossoverStrategy
from chartpilot.signal_engine.strategies.scalp.range_scalp import RangeScalpStrategy
from chartpilot.signal_engine.strategies.scalp.vwap_reversion import VwapReversionStrategy
from chartpilot.ta_engine.indicators import IndicatorSet, VolumeBin, VolumeProfile

_BASE_MS = 1_700_000_000_000


def _attrs(df, symbol="TEST", timeframe="5m"):
    df.attrs["symbol"] = symbol
    df.attrs["timeframe"] = timeframe
    return df


# ---------------------------------------------------------------- EMA Crossover

def _ema_fixture(cross=True):
    n = 30
    close = pd.Series(np.linspace(100, 102, n))
    high, low, open_ = close + 0.1, close - 0.1, close - 0.02
    volume = pd.Series([50.0] * n)
    volume.iloc[-1] = 80.0
    df = _attrs(pd.DataFrame({
        "open_time": np.arange(n) * 60_000 + _BASE_MS, "open": open_, "high": high, "low": low,
        "close": close, "volume": volume,
    }))
    ema9 = pd.Series([100.5] * (n - 1) + [101.6 if cross else 100.6])
    ema21 = pd.Series([101.0] * n)
    ind = IndicatorSet(mode="scalp", rsi14=pd.Series([50.0] * n), atr14=pd.Series([0.2] * n),
                        volume_sma20=pd.Series([50.0] * n), ema9=ema9, ema21=ema21)
    return df, ind


def test_ema_crossover_qualifying_setup():
    df, ind = _ema_fixture()
    signal = EmaCrossoverStrategy().evaluate(df, ind)
    assert signal is not None
    assert signal.direction == "long"
    assert signal.expires_at is not None


def test_ema_crossover_none_without_cross():
    df, ind = _ema_fixture(cross=False)
    assert EmaCrossoverStrategy().evaluate(df, ind) is None


def test_ema_crossover_none_without_rising_volume():
    df, ind = _ema_fixture()
    df.loc[df.index[-1], "volume"] = df["volume"].iloc[-2]  # flat, not rising
    assert EmaCrossoverStrategy().evaluate(df, ind) is None


# ---------------------------------------------------------------- Bollinger Reversion

def _bollinger_fixture(rsi_prev=24.0, rsi_curr=27.0):
    n = 30
    close = pd.Series([100.0] * n)
    high, low, open_ = close + 0.2, close - 0.2, close.copy()
    volume = pd.Series([50.0] * n)
    df = _attrs(pd.DataFrame({
        "open_time": np.arange(n) * 300_000 + _BASE_MS, "open": open_, "high": high, "low": low,
        "close": close, "volume": volume,
    }))
    bb_percent = pd.Series([0.4] * (n - 1) + [-0.1])
    rsi14 = pd.Series([40.0] * n)
    rsi14.iloc[-2], rsi14.iloc[-1] = rsi_prev, rsi_curr
    stoch_k = pd.Series([50.0] * n)
    stoch_d = pd.Series([55.0] * n)
    stoch_k.iloc[-2], stoch_d.iloc[-2] = 12.0, 18.0
    stoch_k.iloc[-1], stoch_d.iloc[-1] = 22.0, 16.0
    vp = VolumeProfile(bins=[VolumeBin(99.9, 100.1, 500.0), VolumeBin(95.0, 96.0, 50.0)])
    ind = IndicatorSet(mode="scalp", rsi14=rsi14, atr14=pd.Series([0.3] * n),
                        volume_sma20=pd.Series([50.0] * n), bb_lower=pd.Series([99.0] * n),
                        bb_mid=pd.Series([100.5] * n), bb_upper=pd.Series([102.0] * n), bb_percent=bb_percent,
                        stoch_k=stoch_k, stoch_d=stoch_d, ema9=pd.Series([100.0] * n),
                        ema21=pd.Series([100.05] * n), volume_profile=vp)
    return df, ind


def test_bollinger_reversion_qualifying_setup():
    df, ind = _bollinger_fixture()
    signal = BollingerReversionStrategy().evaluate(df, ind)
    assert signal is not None
    assert signal.direction == "long"


def test_bollinger_reversion_none_when_rsi_already_reclaimed():
    df, ind = _bollinger_fixture(rsi_prev=27.0, rsi_curr=33.0)  # no longer oversold
    assert BollingerReversionStrategy().evaluate(df, ind) is None


def test_bollinger_reversion_none_without_hvn_proximity():
    df, ind = _bollinger_fixture()
    ind.volume_profile = VolumeProfile(bins=[VolumeBin(50.0, 51.0, 500.0)])  # far from price
    assert BollingerReversionStrategy().evaluate(df, ind) is None


# ---------------------------------------------------------------- VWAP Reversion

def _vwap_fixture(pullback_distance_pct=0.0001):
    n = 30
    vwap = pd.Series(np.linspace(64900, 65200, n))
    close = vwap + 50
    close.iloc[-1] = float(vwap.iloc[-1]) * (1 + pullback_distance_pct)
    open_ = close - 3
    high, low = close + 2, close - 2
    volume = pd.Series([50.0] * n)
    df = _attrs(pd.DataFrame({
        "open_time": np.arange(n) * 300_000 + _BASE_MS, "open": open_, "high": high, "low": low,
        "close": close, "volume": volume,
    }), symbol="BTCUSDT")
    ind = IndicatorSet(mode="scalp", rsi14=pd.Series([55.0] * n), atr14=pd.Series([50.0] * n),
                        volume_sma20=pd.Series([50.0] * n), vwap=vwap)
    return df, ind


def test_vwap_reversion_qualifying_setup():
    df, ind = _vwap_fixture()
    signal = VwapReversionStrategy().evaluate(df, ind)
    assert signal is not None
    assert signal.direction == "long"


def test_vwap_reversion_none_when_too_far_from_vwap():
    df, ind = _vwap_fixture(pullback_distance_pct=0.02)  # 2% away, well outside tolerance
    assert VwapReversionStrategy().evaluate(df, ind) is None


# ---------------------------------------------------------------- Range Scalp

def _range_scalp_fixture(final_low=65900.0):
    n = 30
    close = pd.Series([65950.0] * n)
    close.iloc[15] = 66080.0  # ceiling touch, widens the range
    close.iloc[-1] = 65920.0
    high = close + 20
    low = close - 20
    low.iloc[-1] = final_low
    open_ = close + 5
    volume = pd.Series([50.0] * n)
    df = _attrs(pd.DataFrame({
        "open_time": np.arange(n) * 60_000 + _BASE_MS, "open": open_, "high": high, "low": low,
        "close": close, "volume": volume,
    }), symbol="BTCUSDT", timeframe="1m")
    rsi14 = pd.Series([50.0] * n)
    rsi14.iloc[-2], rsi14.iloc[-1] = 30.0, 34.0
    ind = IndicatorSet(mode="scalp", rsi14=rsi14, atr14=pd.Series([30.0] * n), volume_sma20=pd.Series([50.0] * n))
    return df, ind


def test_range_scalp_qualifying_setup():
    df, ind = _range_scalp_fixture()
    signal = RangeScalpStrategy().evaluate(df, ind)
    assert signal is not None
    assert signal.direction == "long"


def test_range_scalp_suppressed_by_breakout():
    df, ind = _range_scalp_fixture()
    # blow the price far outside the range with volume, which should read as a breakout instead
    df.loc[df.index[-1], "close"] = 66300.0
    df.loc[df.index[-1], "high"] = 66320.0
    df.loc[df.index[-1], "volume"] = 500.0
    assert RangeScalpStrategy().evaluate(df, ind) is None


# ---------------------------------------------------------------- Breakout/Momentum Scalp

def _breakout_scalp_fixture(trigger_close=3208.0):
    close = pd.Series([3200.0] * 10 + [3201, 3202, 3203, 3204, trigger_close])
    high, low = close + 1, close - 1
    volume = pd.Series([50.0] * 14 + [110.0])
    df = _attrs(pd.DataFrame({
        "open_time": np.arange(15) * 60_000 + _BASE_MS, "open": close - 0.5, "high": high, "low": low,
        "close": close, "volume": volume,
    }), symbol="ETHUSDT", timeframe="1m")
    ind = IndicatorSet(mode="scalp", rsi14=pd.Series([50.0] * 15), atr14=pd.Series([1.0] * 15),
                        volume_sma20=pd.Series([50.0] * 15))
    return df, ind


def test_breakout_momentum_scalp_qualifying_setup():
    df, ind = _breakout_scalp_fixture()
    signal = BreakoutMomentumScalpStrategy().evaluate(df, ind)
    assert signal is not None
    assert signal.direction == "long"
    assert signal.reward_risk_ratio == pytest.approx(1.75)


def test_breakout_momentum_scalp_none_inside_range():
    df, ind = _breakout_scalp_fixture(trigger_close=3200.2)
    assert BreakoutMomentumScalpStrategy().evaluate(df, ind) is None
