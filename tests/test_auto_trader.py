import numpy as np
import pandas as pd
import pytest

from chartpilot.trade_engine.auto_trader import AutoTraderWorker, compute_exit_levels, compute_pnl

_BASE_MS = 1_700_000_000_000


class _FakeExchangeClient:
    """Mimics just the surface AutoTraderWorker touches, with no network."""

    def __init__(self, seed_df: pd.DataFrame):
        self.seed_df = seed_df
        self.buy_calls: list[tuple[str, float]] = []
        self.sell_calls: list[tuple[str, float]] = []
        self.buy_response: dict = {"average": 100.0, "filled": 1.0}
        self.sell_response: dict = {"average": 101.0, "filled": 1.0}
        self.buy_error: Exception | None = None
        self.sell_error: Exception | None = None

    def get_candles(self, symbol, timeframe, limit=1000):
        return self.seed_df.copy()

    def place_market_buy_quote(self, symbol, quote_amount):
        self.buy_calls.append((symbol, quote_amount))
        if self.buy_error is not None:
            raise self.buy_error
        return dict(self.buy_response)

    def place_market_sell(self, symbol, amount):
        self.sell_calls.append((symbol, amount))
        if self.sell_error is not None:
            raise self.sell_error
        return dict(self.sell_response)


def _seed_df(n=60, symbol="BTCUSDT", timeframe="1m"):
    close = pd.Series([100.0] * n)
    high, low, open_ = close + 0.2, close - 0.2, close - 0.02
    volume = pd.Series([50.0] * n)
    df = pd.DataFrame({
        "open_time": np.arange(n) * 60_000 + _BASE_MS, "open": open_, "high": high, "low": low,
        "close": close, "volume": volume,
    })
    df.attrs["symbol"] = symbol
    df.attrs["timeframe"] = timeframe
    return df


def _crossover_kline(open_time):
    # A candle whose close sits well above the flat ~100 VWAP baseline, with
    # rising volume, so evaluate() fires a fresh long signal.
    return {"open_time": open_time, "open": 100.0, "high": 101.5, "low": 99.9, "close": 101.2, "volume": 90.0}


def _worker(seed_df, quote_amount=50.0, profit_pct=2.0, loss_pct=1.0):
    fake = _FakeExchangeClient(seed_df)
    worker = AutoTraderWorker(fake, "BTCUSDT", "1m", "vwap_crossover", quote_amount, profit_pct, loss_pct)
    worker._df = seed_df.copy()  # bypass run()'s network seed fetch
    return worker, fake


def test_compute_exit_levels():
    take_profit, stop_loss = compute_exit_levels(100.0, profit_pct=2.0, loss_pct=1.0)
    assert take_profit == pytest.approx(102.0)
    assert stop_loss == pytest.approx(99.0)


def test_compute_pnl_winning_trade():
    pnl_quote, pnl_pct = compute_pnl(entry_price=100.0, exit_price=102.0, quantity=2.0)
    assert pnl_quote == pytest.approx(4.0)
    assert pnl_pct == pytest.approx(2.0)


def test_compute_pnl_losing_trade():
    pnl_quote, pnl_pct = compute_pnl(entry_price=100.0, exit_price=99.0, quantity=2.0)
    assert pnl_quote == pytest.approx(-2.0)
    assert pnl_pct == pytest.approx(-1.0)


def test_opens_position_on_qualifying_kline():
    worker, fake = _worker(_seed_df())
    opened = []
    worker.trade_opened.connect(opened.append)

    worker._on_kline(_crossover_kline(_BASE_MS + 60 * 60_000))

    assert len(fake.buy_calls) == 1
    assert fake.buy_calls[0] == ("BTCUSDT", 50.0)
    assert worker.position is not None
    assert worker.position.entry_price == pytest.approx(100.0)
    assert worker.position.quantity == pytest.approx(1.0)
    assert worker.position.take_profit == pytest.approx(102.0)
    assert worker.position.stop_loss == pytest.approx(99.0)
    assert len(opened) == 1
    assert opened[0] is worker.position


def test_does_not_stack_a_second_position():
    worker, fake = _worker(_seed_df())
    worker._on_kline(_crossover_kline(_BASE_MS + 60 * 60_000))
    assert len(fake.buy_calls) == 1

    worker._on_kline(_crossover_kline(_BASE_MS + 61 * 60_000))
    assert len(fake.buy_calls) == 1  # still just the one — no stacking while a position is open


def test_buy_failure_emits_error_and_leaves_no_position():
    worker, fake = _worker(_seed_df())
    fake.buy_error = RuntimeError("insufficient balance")
    errors = []
    worker.error.connect(errors.append)

    worker._on_kline(_crossover_kline(_BASE_MS + 60 * 60_000))

    assert worker.position is None
    assert len(errors) == 1
    assert "insufficient balance" in errors[0]


def test_closes_on_take_profit_tick():
    worker, fake = _worker(_seed_df())
    worker._on_kline(_crossover_kline(_BASE_MS + 60 * 60_000))
    assert worker.position is not None
    fake.sell_response = {"average": 102.5, "filled": 1.0}
    closed = []
    worker.trade_closed.connect(closed.append)

    worker._on_ticker({"last": 102.5})

    assert worker.position is None
    assert len(fake.sell_calls) == 1
    assert len(closed) == 1
    trade = closed[0]
    assert trade.reason == "take_profit"
    assert trade.exit_price == pytest.approx(102.5)
    assert trade.pnl_quote == pytest.approx(2.5)


def test_closes_on_stop_loss_tick():
    worker, fake = _worker(_seed_df())
    worker._on_kline(_crossover_kline(_BASE_MS + 60 * 60_000))
    fake.sell_response = {"average": 98.7, "filled": 1.0}
    closed = []
    worker.trade_closed.connect(closed.append)

    worker._on_ticker({"last": 98.7})

    assert worker.position is None
    trade = closed[0]
    assert trade.reason == "stop_loss"
    assert trade.pnl_quote < 0


def test_ticker_ignored_when_no_position_open():
    worker, fake = _worker(_seed_df())
    worker._on_ticker({"last": 999.0})
    assert len(fake.sell_calls) == 0


def test_sell_failure_keeps_position_open_for_retry():
    worker, fake = _worker(_seed_df())
    worker._on_kline(_crossover_kline(_BASE_MS + 60 * 60_000))
    fake.sell_error = RuntimeError("network blip")
    errors = []
    worker.error.connect(errors.append)

    worker._on_ticker({"last": 102.5})

    assert worker.position is not None  # not cleared — next tick should retry
    assert len(errors) == 1
