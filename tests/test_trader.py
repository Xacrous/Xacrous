import asyncio

import pytest

from scalper.book import Book
from scalper.brokers import PaperBroker
from scalper.flow import Candle, Candles, TradeFlow
from scalper.market import MarketInfo
from scalper.settings import TradeParams
from scalper.signals import SignalParams
from scalper.store import Store
from scalper.trader import BUYING, HOLDING, IDLE, Trader

M = MarketInfo("BTCUSDT", "BTC/USDT", "BTC", "USDT", tick=0.01, step=0.00001, min_qty=0.00001, min_notional=5)


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def run(coro):
    return asyncio.run(coro)


def book(bid, ask, bq=5.0, aq=1.0, levels=5):
    return Book([(bid - i * 0.01, bq) for i in range(levels)], [(ask + i * 0.01, aq) for i in range(levels)])


def make(clock, **kw):
    kw = {"order_quote": 100, "cooldown_s": 0, "exit_mode": "fixed", **kw}
    params = TradeParams(signal=SignalParams(confirm_s=0, min_trades=1, trend_filter=False), **kw)
    flow, candles = TradeFlow(), Candles()
    for i in range(20):  # 1m candles with ~1% range so the volatility filter passes
        candles.upsert(Candle(i * 60_000, 100, 100.5, 99.5, 100, 1, True))
    store = Store(":memory:")
    broker = PaperBroker(M, 1000, 0.10, 0.10)
    t = Trader(params, M, broker, store, flow, candles, clock=clock)
    run(t.start())
    t.set_enabled(True)
    return t, broker, flow, store


def buy_pressure(flow, clock, price=100.0):
    for _ in range(5):
        flow.add(clock.t, price, 1.0, False)  # aggressive buys


def feed(t, broker, b):
    b.ts = t.clock()
    broker.on_book(b)
    run(t.step(b))


def test_take_profit_nets_at_least_min_profit_after_fees():
    clock = Clock()
    t, broker, flow, store = make(clock)
    buy_pressure(flow, clock)
    feed(t, broker, book(100.00, 100.02))
    assert t.state == BUYING and t.buy["price"] == 100.01   # one tick inside the spread
    broker.on_trade(100.00)                                  # trade prints below our bid -> filled
    feed(t, broker, book(100.00, 100.02))
    assert t.state == HOLDING
    tp = t.pos["tp_price"]
    assert tp > 100.01 * 1.0032 - 0.01                      # 0.12% net + 2 x 0.10% fees
    broker.on_trade(tp + 0.01)                               # trade prints above TP -> filled
    clock.t += 5
    feed(t, broker, book(tp, tp + 0.02))
    assert t.state == IDLE
    (trade,) = store.trades()
    assert trade["reason"] == "take profit"
    assert trade["pnl_pct"] >= 0.12
    base, quote = run(broker.balances())
    assert base < 0.00001                                    # only rounding dust is left
    assert quote + base * trade["entry_price"] == pytest.approx(1000 + trade["pnl"], abs=1e-6)


def test_stop_loss_sells_at_market():
    clock = Clock()
    t, broker, flow, store = make(clock, stop_loss_pct=0.30)
    buy_pressure(flow, clock)
    feed(t, broker, book(100.00, 100.02))
    broker.on_trade(99.99)
    feed(t, broker, book(100.00, 100.02))
    assert t.state == HOLDING
    clock.t += 3
    feed(t, broker, book(99.70, 99.72))                      # bid below 100.01 * (1 - 0.3%)
    assert t.state == IDLE
    (trade,) = store.trades()
    assert trade["reason"] == "stop loss" and trade["pnl"] < 0
    assert all(o["status"] != "open" for o in broker.orders.values())  # TP cancelled
    assert broker.base == pytest.approx(0, abs=1e-5)


def test_time_limit_exit():
    clock = Clock()
    t, broker, flow, store = make(clock, max_hold_s=60)
    buy_pressure(flow, clock)
    feed(t, broker, book(100.00, 100.02))
    broker.on_trade(99.99)
    feed(t, broker, book(100.00, 100.02))
    clock.t += 61
    buy_pressure(flow, clock)
    feed(t, broker, book(100.05, 100.07))
    assert store.trades()[0]["reason"] == "time limit"


def test_unfilled_buy_is_cancelled_after_timeout():
    clock = Clock()
    t, broker, flow, store = make(clock, entry_timeout_s=10)
    buy_pressure(flow, clock)
    feed(t, broker, book(100.00, 100.02))
    assert t.state == BUYING
    clock.t += 11
    buy_pressure(flow, clock)
    feed(t, broker, book(100.00, 100.02))
    assert t.state == IDLE and store.trades() == []
    assert broker.quote == pytest.approx(1000)  # reserved funds released


def test_no_entry_without_signal_or_when_paused():
    clock = Clock()
    t, broker, flow, _ = make(clock)
    feed(t, broker, book(100.00, 100.02, bq=1, aq=5))   # ask-heavy book, no flow
    assert t.state == IDLE and not t.snap.buy
    t.set_enabled(False)
    buy_pressure(flow, clock)
    feed(t, broker, book(100.00, 100.02))
    assert t.state == IDLE and t.blocker == "paused"


def test_consecutive_losses_halt_trading():
    clock = Clock()
    t, broker, flow, store = make(clock, max_consecutive_losses=2, max_daily_loss=1000)
    for _ in range(2):
        buy_pressure(flow, clock)
        feed(t, broker, book(100.00, 100.02))
        broker.on_trade(99.99)
        feed(t, broker, book(100.00, 100.02))
        clock.t += 1
        feed(t, broker, book(99.60, 99.62))
        feed(t, broker, book(100.00, 100.02))  # back to normal
        clock.t += 1
    assert len(store.trades()) == 2
    assert t.halt_reason and not t.enabled
    buy_pressure(flow, clock)
    feed(t, broker, book(100.00, 100.02))
    assert t.state == IDLE


def test_order_size_below_exchange_minimum_is_refused():
    clock = Clock()
    t, broker, flow, _ = make(clock, order_quote=4)
    buy_pressure(flow, clock)
    feed(t, broker, book(100.00, 100.02))
    assert t.state == IDLE and "minimum" in t.blocker


def test_target_gross_includes_fees():
    clock = Clock()
    t, *_ = make(clock)
    assert t.target_gross_pct() == pytest.approx(((1.0012) / (0.999 * 0.999) - 1) * 100)
    t.p.exit_mode, t.taker = "trail", 0.002          # trailing exits pay the taker fee
    assert t.target_gross_pct() == pytest.approx(((1.0012) / (0.999 * 0.998) - 1) * 100)


def test_bearish_flip_must_persist_before_exit():
    clock = Clock()
    t, broker, flow, store = make(clock)
    buy_pressure(flow, clock)
    feed(t, broker, book(100.00, 100.02))
    broker.on_trade(99.99)
    feed(t, broker, book(100.00, 100.02))
    assert t.state == HOLDING
    bearish = lambda: book(100.00, 100.02, bq=1, aq=20)  # noqa: E731
    for _ in range(5):
        flow.add(clock.t, 100.0, 5.0, True)              # aggressive sells
    feed(t, broker, bearish())
    clock.t += 1
    feed(t, broker, bearish())
    assert t.state == HOLDING                            # a 1 s blip does not exit
    clock.t += 3
    feed(t, broker, bearish())
    assert t.state == IDLE and store.trades()[0]["reason"] == "order book turned bearish"


def test_lot_rounding_dust_does_not_inflate_take_profit():
    # Real BTCUSDT-like rules: step 0.00001 BTC at ~$100k, so a $20 order is only 20 steps.
    clock = Clock()
    m = MarketInfo("BTCUSDT", "BTC/USDT", "BTC", "USDT", tick=0.01, step=0.00001, min_qty=0.00001, min_notional=5)
    params = TradeParams(order_quote=20, cooldown_s=0, exit_mode="fixed",
                         signal=SignalParams(confirm_s=0, min_trades=1, trend_filter=False))
    flow, candles = TradeFlow(), Candles()
    for i in range(20):
        candles.upsert(Candle(i * 60_000, 100_000, 101_000, 99_000, 100_000, 1, True))
    store = Store(":memory:")
    broker = PaperBroker(m, 1000, 0.10, 0.10)
    t = Trader(params, m, broker, store, flow, candles, clock=clock)
    run(t.start())
    t.set_enabled(True)
    for _ in range(5):
        flow.add(clock.t, 100_000, 0.01, False)
    b = Book([(100_000 - i * 0.01, 1) for i in range(5)], [(100_000.01 + i * 0.01, 0.1) for i in range(5)])
    feed(t, broker, b)
    broker.on_trade(99_999.0)
    feed(t, broker, b)
    pos = t.pos
    assert pos["sell_qty"] < pos["qty"]                                   # some dust exists
    gain = (pos["tp_price"] / pos["avg"] - 1) * 100
    assert gain == pytest.approx(t.target_gross_pct(), abs=0.01)          # not inflated by the dust
    broker.on_trade(pos["tp_price"] + 1)
    feed(t, broker, b)
    (trade,) = store.trades()
    assert trade["pnl_pct"] >= 0.12
    carry = store.get("paper:BTCUSDT:carry")
    assert carry["qty"] > 0 and carry["cost"] > 0                         # dust remembered for next sell


def test_low_priced_coin_with_coarse_tick():
    # ARKUSDT-like: price ~0.35, tick 0.0001 (~2.9 bps), lot step 0.1
    clock = Clock()
    m = MarketInfo("ARKUSDT", "ARK/USDT", "ARK", "USDT", tick=0.0001, step=0.1, min_qty=0.1, min_notional=5)
    params = TradeParams(symbol="ARKUSDT", order_quote=20, cooldown_s=0, exit_mode="fixed",
                         signal=SignalParams(confirm_s=0, min_trades=1, trend_filter=False))
    flow, candles = TradeFlow(), Candles()
    for i in range(20):
        candles.upsert(Candle(i * 60_000, 0.35, 0.352, 0.348, 0.35, 1, True))
    store = Store(":memory:")
    broker = PaperBroker(m, 1000, 0.10, 0.10)
    t = Trader(params, m, broker, store, flow, candles, clock=clock)
    run(t.start())
    t.set_enabled(True)
    for _ in range(5):
        flow.add(clock.t, 0.35, 100, False)
    b = Book([(0.3500 - i * 0.0001, 5000) for i in range(5)], [(0.3501 + i * 0.0001, 800) for i in range(5)])
    feed(t, broker, b)
    assert t.buy["price"] == pytest.approx(0.35)            # 1-tick spread: joins the bid, never crosses
    broker.on_trade(0.3499)
    feed(t, broker, b)
    pos = t.pos
    net = (pos["tp_price"] * (1 - t.maker) / pos["unit_cost"] - 1) * 100
    assert 0.12 <= net < 0.12 + 0.03                        # at most one 2.9 bps tick above the target
    assert pos["tp_price"] == pytest.approx(round(pos["tp_price"], 4))   # on the tick grid
    assert pos["sell_qty"] == pytest.approx(round(pos["sell_qty"], 1))  # on the lot grid
    broker.on_trade(pos["tp_price"] + 0.0001)
    feed(t, broker, b)
    assert store.trades()[0]["pnl_pct"] >= 0.12


# ----- trail mode: 0.12% is a floor, not a cap ------------------------------------------
def enter_trail(clock, **kw):
    t, broker, flow, store = make(clock, exit_mode="trail", trail_pct=0.30, max_hold_s=60, **kw)
    buy_pressure(flow, clock)
    feed(t, broker, book(100.00, 100.02))
    broker.on_trade(99.99)
    feed(t, broker, book(100.00, 100.02))
    assert t.state == HOLDING and t.pos["tp"] is None      # no resting sell capping the profit
    return t, broker, flow, store


def test_trail_lets_a_big_move_run_past_min_profit():
    clock = Clock()
    t, broker, flow, store = enter_trail(clock)
    floor = t.pos["floor_price"]
    feed(t, broker, book(floor, floor + 0.02))
    assert not t.pos["trailing"]                                 # at the floor exactly: keep holding
    feed(t, broker, book(floor + 0.01, floor + 0.03))
    assert t.pos["trailing"] and t.pos["trail_stop"] == floor   # minimum profit locked
    price = floor
    for _ in range(200):                                         # a strong run: +20%, slowly over 10 min
        price *= 1.001
        clock.t += 3
        feed(t, broker, book(round(price, 2), round(price, 2) + 0.02, bq=1, aq=20))  # even a bearish book
    assert t.state == HOLDING                                    # no time limit / bearish exit while trailing
    peak = t.pos["peak"]
    clock.t += 1
    feed(t, broker, book(round(peak * 0.996, 2), round(peak * 0.996, 2) + 0.02))   # 0.4% pullback
    assert t.state == IDLE
    (trade,) = store.trades()
    assert trade["reason"].startswith("trailing stop")
    assert trade["pnl_pct"] > 19                                 # kept most of the +20% move


def test_trail_never_exits_below_min_profit_once_reached():
    clock = Clock()
    t, broker, flow, store = enter_trail(clock)
    floor = t.pos["floor_price"]
    feed(t, broker, book(floor + 0.05, floor + 0.07))           # just above the floor: trailing
    clock.t += 1
    feed(t, broker, book(floor, floor + 0.02))                  # back to the floor -> sell there
    (trade,) = store.trades()
    assert trade["pnl_pct"] >= 0.12 - 1e-9


def test_trail_still_uses_stop_loss_before_min_profit():
    clock = Clock()
    t, broker, flow, store = enter_trail(clock)
    clock.t += 1
    feed(t, broker, book(99.60, 99.62))
    assert store.trades()[0]["reason"] == "stop loss"


def test_trail_gap_below_floor_never_sells_under_min_profit():
    clock = Clock()
    t, broker, flow, store = enter_trail(clock)
    floor = t.pos["floor_price"]
    feed(t, broker, book(floor + 0.10, floor + 0.12))           # trailing, floor locked
    clock.t += 1
    feed(t, broker, book(floor - 0.03, floor - 0.01))           # price gaps straight through the floor
    assert t.state == HOLDING and store.trades() == []          # nothing sold below the minimum
    assert t.pos["tp"]["status"] == "open" and t.pos["tp"]["price"] >= t.pos["tp_price"]
    broker.on_trade(t.pos["tp"]["price"] + 0.01)                # price comes back
    clock.t += 1
    feed(t, broker, book(floor, floor + 0.02))
    assert store.trades()[0]["pnl_pct"] >= 0.12


def test_trail_exit_ioc_partial_fill_keeps_the_rest_resting():
    clock = Clock()
    t, broker, flow, store = enter_trail(clock)
    floor = t.pos["floor_price"]
    feed(t, broker, book(floor + 0.10, floor + 0.12))
    clock.t += 1
    qty = t.pos["sell_qty"]
    thin = Book([(floor, qty / 2), (floor - 0.05, 100)], [(floor + 0.02, 1)])  # only half fits at >= floor
    feed(t, broker, thin)
    assert t.state == HOLDING and t.pos["sell_qty"] == pytest.approx(broker.m.qty_down(qty / 2), abs=1e-5)
    assert t.pos["sells"][0]["filled"] == pytest.approx(qty / 2, rel=1e-3)
