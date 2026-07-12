import numpy as np
import pandas as pd
import pytest

from chartpilot.signal_engine.strategies.trade.vwap_crossover import VwapCrossoverStrategy
from chartpilot.ta_engine.indicators import IndicatorSet

_BASE_MS = 1_700_000_000_000


def _attrs(df, symbol="BTCUSDT", timeframe="1m"):
    df.attrs["symbol"] = symbol
    df.attrs["timeframe"] = timeframe
    return df


def _vwap_fixture(cross=True, extended=False):
    n = 10
    if cross:
        close = pd.Series([100.0] * 8 + [99.8, 101.0])
    else:
        close = pd.Series([101.0] * n)  # already above VWAP the whole window, no cross
    if extended:
        close.iloc[-1] = 106.0  # far past VWAP by the time we see it
    high, low, open_ = close + 0.3, close - 0.3, close - 0.05
    volume = pd.Series([50.0] * 9 + [90.0])
    df = _attrs(pd.DataFrame({
        "open_time": np.arange(n) * 60_000 + _BASE_MS, "open": open_, "high": high, "low": low,
        "close": close, "volume": volume,
    }))
    ind = IndicatorSet(
        mode="trade",
        rsi14=pd.Series([50.0] * n),
        atr14=pd.Series([1.0] * n),
        volume_sma20=pd.Series([50.0] * n),
        vwap=pd.Series([100.2] * n),
    )
    return df, ind


def test_vwap_crossover_qualifying_setup():
    df, ind = _vwap_fixture()
    signal = VwapCrossoverStrategy().evaluate(df, ind)
    assert signal is not None
    assert signal.direction == "long"
    assert signal.mode == "trade"
    assert signal.stop_loss < signal.entry < signal.take_profit
    assert signal.reward_risk_ratio == pytest.approx(1.75)
    assert signal.expires_at is not None


def test_vwap_crossover_none_without_cross():
    df, ind = _vwap_fixture(cross=False)
    assert VwapCrossoverStrategy().evaluate(df, ind) is None


def test_vwap_crossover_none_when_overextended():
    df, ind = _vwap_fixture(extended=True)
    assert VwapCrossoverStrategy().evaluate(df, ind) is None


def test_vwap_crossover_stop_is_atr_based():
    df, ind = _vwap_fixture()
    signal = VwapCrossoverStrategy().evaluate(df, ind)
    entry = float(df["close"].iloc[-1])
    atr = 1.0
    assert signal.stop_loss == pytest.approx(entry - atr * 1.75)


def test_vwap_crossover_is_long_only():
    # No bearish branch exists at all — spot can't short, so evaluate()
    # should never surface a "short" direction regardless of price action.
    df, ind = _vwap_fixture()
    signal = VwapCrossoverStrategy().evaluate(df, ind)
    assert signal.direction == "long"
