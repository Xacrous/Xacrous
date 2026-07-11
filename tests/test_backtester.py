import numpy as np
import pandas as pd
import pytest

from chartpilot.signal_engine.backtester import BacktestResult, BacktestTrade, run_backtest
from chartpilot.signal_engine.registry import get_strategy
from chartpilot.ta_engine.indicators import MIN_CANDLES


def _trade(confidence, outcome, reward_risk=2.0):
    return BacktestTrade(
        entry_index=0, entry_time_ms=0, direction="long", entry=100.0, take_profit=110.0,
        stop_loss=95.0, confidence=confidence, reward_risk_ratio=reward_risk, outcome=outcome, exit_time_ms=1000,
    )


def test_backtest_result_aggregates_wins_and_losses():
    trades = [_trade(80, "hit_tp"), _trade(75, "hit_sl"), _trade(70, "hit_tp")]
    result = BacktestResult(strategy_id="x", symbol="BTCUSDT", timeframe="4h", trades=trades)
    assert result.total_trades == 3
    assert result.win_rate == pytest.approx(2 / 3)
    assert result.avg_reward_risk == pytest.approx(2.0)
    # expectancy: (2 wins * +2R + 1 loss * -1R) / 3 resolved trades
    assert result.expectancy == pytest.approx((2 + 2 - 1) / 3)


def test_backtest_result_ignores_pending_trades_in_win_rate():
    trades = [_trade(80, "hit_tp"), _trade(75, "pending")]
    result = BacktestResult(strategy_id="x", symbol="BTCUSDT", timeframe="4h", trades=trades)
    assert result.total_trades == 2
    assert result.win_rate == 1.0  # only the resolved trade counts


def test_backtest_result_empty_has_none_stats():
    result = BacktestResult(strategy_id="x", symbol="BTCUSDT", timeframe="4h", trades=[])
    assert result.win_rate is None
    assert result.avg_reward_risk is None
    assert result.expectancy is None
    assert result.win_rate_near_confidence(75) is None


def test_win_rate_near_confidence_filters_by_band():
    trades = [_trade(80, "hit_tp"), _trade(20, "hit_sl")]
    result = BacktestResult(strategy_id="x", symbol="BTCUSDT", timeframe="4h", trades=trades)
    near = result.win_rate_near_confidence(78, band=10)
    assert near == (1.0, 1)  # only the confidence=80 trade is within band of 78


def test_run_backtest_returns_empty_for_insufficient_history():
    n = MIN_CANDLES["swing"] - 10
    df = pd.DataFrame({
        "open_time": np.arange(n) * 3600_000, "open": [100.0] * n, "high": [101.0] * n,
        "low": [99.0] * n, "close": [100.0] * n, "volume": [10.0] * n,
    })
    strategy = get_strategy("trend_following_ma_cross")
    result = run_backtest(df, "swing", strategy)
    assert result.total_trades == 0


def test_run_backtest_finds_and_resolves_trades_on_trending_data():
    np.random.seed(3)
    n = 400
    t = np.arange(n)
    trend = np.linspace(100, 220, n)
    cycle = 6 * np.sin(t / 8.0)
    close = trend + cycle + np.random.normal(0, 0.4, n)
    high = close + np.random.uniform(0.3, 1.0, n)
    low = close - np.random.uniform(0.3, 1.0, n)
    open_ = close - np.random.uniform(-0.5, 0.5, n)
    volume = np.random.uniform(80, 160, n)
    df = pd.DataFrame({
        "open_time": np.arange(n) * 3600_000 + 1_700_000_000_000,
        "open": open_, "high": high, "low": low, "close": close, "volume": volume,
    })
    df.attrs["symbol"] = "BTCUSDT"
    df.attrs["timeframe"] = "4h"

    strategy = get_strategy("macd_momentum")
    result = run_backtest(df, "swing", strategy)
    assert result.total_trades >= 1
    for trade in result.trades:
        assert trade.outcome in ("hit_tp", "hit_sl", "expired", "pending")
        assert trade.direction in ("long", "short")
