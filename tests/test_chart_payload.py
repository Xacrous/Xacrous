import numpy as np
import pandas as pd
import pytest

from chartpilot.chart_view.chart_widget import build_chart_payload
from chartpilot.signal_engine.registry import get_strategy
from chartpilot.ta_engine.indicators import compute


def _swing_df(n=210):
    close = pd.Series(np.linspace(100, 110, n))
    return pd.DataFrame({
        "open_time": np.arange(n) * 3600_000, "open": close - 0.1, "high": close + 0.2,
        "low": close - 0.2, "close": close, "volume": [50.0] * n,
    })


def _swing_df_with_fib_impulse():
    # 150 flat lead-in candles (so compute(mode="swing")'s 200-candle SMA200
    # minimum is satisfied) followed by a clean impulse-then-pullback swing
    # in the most recent 60 bars, so detect_fibonacci_retracement's default
    # lookback=60 finds a qualifying move.
    lead_in_lows = [10.0] * 150
    impulse_lows = [10] * 5 + list(np.linspace(10, 5, 10))[:-1] + list(np.linspace(5, 20, 20)) + [19] * 26
    lows = lead_in_lows + impulse_lows
    highs = [low + 1 for low in lows]
    close = pd.Series([(h + low) / 2 for h, low in zip(highs, lows)])
    n = len(close)
    return pd.DataFrame({
        "open_time": np.arange(n) * 3600_000, "open": close, "high": highs, "low": lows,
        "close": close, "volume": [10.0] * n,
    })


def _scalp_df(n=60):
    close = pd.Series(np.linspace(100, 105, n))
    return pd.DataFrame({
        "open_time": np.arange(n) * 60_000, "open": close - 0.05, "high": close + 0.1,
        "low": close - 0.1, "close": close, "volume": [50.0] * n,
    })


@pytest.fixture
def swing_indicators():
    return compute(_swing_df(), mode="swing")


@pytest.fixture
def scalp_indicators():
    return compute(_scalp_df(), mode="scalp")


def test_unfiltered_swing_shows_all_smas_pivots_and_fib():
    df = _swing_df_with_fib_impulse()
    indicators = compute(df, mode="swing")
    payload = build_chart_payload(df, indicators, required_indicators=None)
    assert set(payload["overlays"]) == {"sma20", "sma50", "sma200"}
    assert payload["pivots"] is not None
    assert payload["fibonacci"] is not None


def test_unfiltered_scalp_shows_everything(scalp_indicators):
    payload = build_chart_payload(_scalp_df(), scalp_indicators, required_indicators=None)
    assert set(payload["overlays"]) == {"ema9", "ema21", "bb_lower", "bb_mid", "bb_upper", "vwap"}
    assert payload["volume_profile"] is not None


def test_trend_following_scopes_to_its_own_indicators(swing_indicators):
    strategy = get_strategy("trend_following_ma_cross")
    payload = build_chart_payload(_swing_df(), swing_indicators, required_indicators=strategy.required_indicators)
    # trend_following declares sma50/sma200 but not sma20 or fibonacci
    assert set(payload["overlays"]) == {"sma50", "sma200"}
    assert payload["pivots"] is not None  # declares "pivots"
    assert payload["fibonacci"] is None  # does not declare "fibonacci"


def test_pullback_fib_shows_fib_lines_and_sma50_only():
    df = _swing_df_with_fib_impulse()
    indicators = compute(df, mode="swing")
    strategy = get_strategy("pullback_fibonacci")
    payload = build_chart_payload(df, indicators, required_indicators=strategy.required_indicators)
    assert set(payload["overlays"]) == {"sma50"}
    assert payload["fibonacci"] is not None
    assert payload["pivots"] is None  # does not declare "pivots"


def test_ema_crossover_hides_bollinger_and_vwap(scalp_indicators):
    strategy = get_strategy("ema_crossover_momentum")
    payload = build_chart_payload(_scalp_df(), scalp_indicators, required_indicators=strategy.required_indicators)
    assert set(payload["overlays"]) == {"ema9", "ema21"}


def test_vwap_reversion_shows_only_vwap(scalp_indicators):
    strategy = get_strategy("vwap_reversion")
    payload = build_chart_payload(_scalp_df(), scalp_indicators, required_indicators=strategy.required_indicators)
    assert set(payload["overlays"]) == {"vwap"}


def test_bollinger_reversion_shows_bands_and_volume_profile(scalp_indicators):
    strategy = get_strategy("bollinger_mean_reversion")
    payload = build_chart_payload(_scalp_df(), scalp_indicators, required_indicators=strategy.required_indicators)
    assert set(payload["overlays"]) == {"bb_lower", "bb_mid", "bb_upper"}
    assert payload["volume_profile"] is not None


def test_range_scalp_has_no_overlay_mappable_indicators(scalp_indicators):
    # range_scalp's required_indicators are ["rsi14", "atr14"] — neither
    # maps to a drawable chart overlay, so it correctly shows none.
    strategy = get_strategy("range_scalp")
    payload = build_chart_payload(_scalp_df(), scalp_indicators, required_indicators=strategy.required_indicators)
    assert payload["overlays"] == {}
    assert payload["volume_profile"] is None


def test_vwap_crossover_trade_mode_shows_only_vwap():
    df = _scalp_df()
    indicators = compute(df, mode="trade")
    strategy = get_strategy("vwap_crossover")
    payload = build_chart_payload(df, indicators, required_indicators=strategy.required_indicators)
    assert set(payload["overlays"]) == {"vwap"}
