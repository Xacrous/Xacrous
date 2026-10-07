import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from scalper.book import Book
from scalper.brokers import BinanceBroker, OrderRejected
from scalper.engine import Engine
from scalper.feed import BinanceFeed
from scalper.flow import Candles, TradeFlow
from scalper.market import MarketInfo, from_ccxt
from scalper.settings import EnvSettings, TradeParams
from scalper.store import Store
from scalper.web import create_app

M = MarketInfo("ETHBTC", "ETH/BTC", "ETH", "BTC", tick=0.00001, step=0.0001, min_qty=0.0001, min_notional=0.0001)


# ----- book maths ---------------------------------------------------------------------
def test_book_metrics():
    b = Book([(100.0, 9.0), (99.99, 1.0)], [(100.02, 1.0), (100.03, 1.0)], time.time())
    assert b.mid == pytest.approx(100.01) and b.spread_bps == pytest.approx(2.0, rel=1e-3)
    assert b.microprice > b.mid and 0 < b.micro_skew() <= 1          # big bid -> leans up
    assert b.imbalance(10) > 0.5
    assert b.market_sell_price(9.5) == pytest.approx((9 * 100 + 0.5 * 99.99) / 9.5)


def test_trade_flow_sides():
    f = TradeFlow()
    now = time.time()
    f.add(now, 10, 3, False)   # aggressive buy
    f.add(now, 10, 1, True)    # aggressive sell
    s = f.stats(30, now)
    assert s["flow"] == pytest.approx(0.5) and s["count"] == 2


def test_market_rounding_and_symbols():
    assert M.price_up(0.0512345) == pytest.approx(0.05124)
    assert M.price_down(0.0512399) == pytest.approx(0.05123)
    assert M.qty_down(1.23456) == pytest.approx(1.2345)
    p = TradeParams.from_dict({"symbol": " eth/btc "})
    assert p.symbol == "ETHBTC"
    with pytest.raises(ValueError):
        TradeParams(symbol="BTC USDT").validate()
    with pytest.raises(ValueError):
        TradeParams(min_profit_pct=0.1).validate()


class FakeMarkets:
    precisionMode = 4
    markets_by_id = {"ETHBTC": [{"symbol": "ETH/BTC", "base": "ETH", "quote": "BTC", "spot": True, "active": True,
                                 "precision": {"price": 0.00001, "amount": 0.0001},
                                 "limits": {"amount": {"min": 0.0001}, "cost": {"min": 0.0001}}}]}


def test_from_ccxt():
    m = from_ccxt(FakeMarkets(), "ethbtc")
    assert (m.ccxt_symbol, m.base, m.quote, m.tick) == ("ETH/BTC", "ETH", "BTC", 0.00001)
    with pytest.raises(ValueError):
        from_ccxt(FakeMarkets(), "NOPEUSDT")


def test_feed_parses_binance_messages():
    feed = BinanceFeed("ETHBTC", TradeFlow(), Candles(), None, "ETH/BTC")
    assert "ethbtc@depth20@100ms" in feed.url and "stream.binance.com" in feed.url
    feed._handle({"stream": "ethbtc@depth20@100ms", "data": {"bids": [["0.05", "2"], ["0.04999", "0"]],
                                                               "asks": [["0.05001", "1"]]}})
    assert feed.book.bids == [(0.05, 2.0)] and feed.book.best_ask == 0.05001   # zero-size levels dropped
    feed._handle({"stream": "ethbtc@aggTrade", "data": {"e": "aggTrade", "T": time.time() * 1000, "p": "0.05",
                                                        "q": "1.5", "m": False}})
    assert feed.flow.stats(30, time.time())["buy_quote"] == pytest.approx(0.075)
    feed._handle({"stream": "ethbtc@kline_1m", "data": {"e": "kline", "k": {"t": 0, "o": "1", "h": "2", "l": "0.5",
                                                                            "c": "1.5", "v": "3", "x": True}}})
    assert feed.candles.closed()[0].high == 2.0
    assert "testnet" in BinanceFeed("ETHBTC", TradeFlow(), Candles(), None, "ETH/BTC", testnet=True).url


# ----- live broker against a fake async ccxt exchange -------------------------------------------
class FakeAsyncBinance:
    def __init__(self):
        self.created = []

    async def create_order(self, symbol, type_, side, qty, price, params):
        import ccxt
        if params.get("postOnly") and side == "buy" and price >= 0.051:
            raise ccxt.InvalidOrder("binance Order would immediately match and take.")
        self.created.append((type_, side, qty, price, params))
        return {"id": "42", "side": side, "type": type_, "status": "open", "amount": qty, "price": price,
                "filled": 0, "cost": 0}

    async def fetch_order(self, oid, symbol):
        return {"id": oid, "side": "buy", "type": "limit", "status": "closed", "amount": 1.0, "price": 0.05,
                "filled": 1.0, "cost": 0.05}

    async def fetch_my_trades(self, symbol, params):
        return [{"fee": {"cost": 0.001, "currency": "ETH"}, "cost": 0.05, "takerOrMaker": "maker"}]

    async def fetch_trading_fee(self, symbol):
        return {"maker": 0.00075, "taker": 0.00075}


def test_binance_broker_post_only_and_fees():
    ex = FakeAsyncBinance()
    br = BinanceBroker(ex, M)
    o = asyncio.run(br.limit_buy_maker(1.0, 0.05))
    assert o["status"] == "open"
    _, _, _, _, params = ex.created[0]
    assert params["postOnly"] is True and params["newClientOrderId"].startswith("x-scalp")
    with pytest.raises(OrderRejected):
        asyncio.run(br.limit_buy_maker(1.0, 0.052))
    filled = asyncio.run(br.fetch("42"))
    assert filled["fee_base"] == pytest.approx(0.001) and filled["fee_quote"] == pytest.approx(0.00005)
    assert asyncio.run(br.fees()) == (0.00075, 0.00075)


# ----- web safety -------------------------------------------------------------------------------
def test_web_blocks_other_hosts_and_cross_site_posts(tmp_path):
    env = EnvSettings(feed="demo", data_dir=str(tmp_path))
    eng = Engine(env, Store(":memory:"))
    client = TestClient(create_app(eng, start_engine=False), base_url="http://127.0.0.1:8080")
    assert client.get("/api/trades").status_code == 200
    assert client.get("/api/trades", headers={"host": "evil.example"}).status_code == 403     # DNS rebinding
    assert client.post("/api/pause").status_code == 403                                       # no header
    h = {"X-Requested-With": "scalper"}
    assert client.post("/api/pause", headers={**h, "Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/pause", headers=h).status_code == 200
    assert client.get("/docs").status_code == 404
    assert "frame-ancestors 'none'" in client.get("/").headers["content-security-policy"]


def test_env_refuses_public_host_and_live_without_keys():
    with pytest.raises(ValueError):
        EnvSettings(host="0.0.0.0").validate()
    with pytest.raises(ValueError):
        EnvSettings(mode="live").validate()
    with pytest.raises(ValueError):
        EnvSettings(mode="live", api_key="k", api_secret="s", feed="demo").validate()
    EnvSettings(mode="live", api_key="k", api_secret="s").validate()


def test_live_broker_refuses_withdrawal_enabled_key():
    class Ex(FakeAsyncBinance):
        async def load_markets(self):
            pass

        async def fetch_balance(self):
            return {"free": {}}

        async def sapiGetAccountApiRestrictions(self):
            return {"enableWithdrawals": True, "enableSpotAndMarginTrading": True, "ipRestrict": False}

    with pytest.raises(PermissionError, match="WITHDRAW"):
        asyncio.run(BinanceBroker(Ex(), M).start())
    asyncio.run(BinanceBroker(Ex(), M, testnet=True).start())  # testnet has no such endpoint; skipped


def test_engine_runs_on_demo_feed(tmp_path):
    async def go():
        eng = Engine(EnvSettings(feed="demo", data_dir=str(tmp_path)), Store(":memory:"))
        await eng.start()
        await asyncio.sleep(0.6)
        s = await eng.status()
        await eng.stop()
        return s
    s = asyncio.run(go())
    assert s["error"] is None and s["book"] and s["trader"]["state"] == "IDLE"
    assert s["balances"]["quote"] == 1000.0 and s["trader"]["enabled"] is False  # always starts paused


def test_binance_ioc_sell_sends_time_in_force():
    ex = FakeAsyncBinance()
    asyncio.run(BinanceBroker(ex, M).limit_sell_ioc(1.23456, 0.05))
    type_, side, qty, price, params = ex.created[0]
    assert (type_, side, qty, price, params["timeInForce"]) == ("limit", "sell", 1.2345, 0.05, "IOC")
