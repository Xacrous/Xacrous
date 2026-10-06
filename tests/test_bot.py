import time

import pytest
from fastapi.testclient import TestClient

from app.backtest import Candle, run_backtest
from app.broker import PaperBroker
from app.config import Settings
from app.data import DAY_MS, closed_only
from app.engine import Engine
from app.main import create_app
from app.store import Store
from app.strategy import StrategyParams, evaluate, sma

P = StrategyParams(sma_length=5, entry_band=0.06, exit_band=0.04)


def candles(closes, start_ms=1_600_000_000_000):
    return [Candle(start_ms + i * DAY_MS, c, c * 1.01, c * 0.99, c) for i, c in enumerate(closes)]


class FakeMarket:
    def __init__(self, closes):
        now = int(time.time() * 1000)
        self.data = candles(closes, start_ms=now - (len(closes) + 1) * DAY_MS)

    def closed(self):
        return self.data

    def price(self):
        return self.data[-1].close


def make_engine(closes, **kw):
    s = Settings(params=P, **kw)
    store = Store(":memory:")
    return Engine(s, store, PaperBroker(store, 10_000, 0.001), FakeMarket(closes))


def test_sma():
    assert sma([1, 2, 3, 4], 2) == [None, 1.5, 2.5, 3.5]


def test_evaluate_bands_and_hysteresis():
    flat = [100] * 5
    assert evaluate(flat, False, P).action == "HOLD"
    assert evaluate([100] * 4 + [130], False, P).action == "BUY"     # sma 106, entry 112.36
    assert evaluate([100] * 4 + [105], True, P).action == "HOLD"     # between bands: keep holding
    assert evaluate([100] * 4 + [105], False, P).action == "HOLD"    # between bands: stay flat
    assert evaluate([100] * 4 + [80], True, P).action == "SELL"      # sma 96, exit 92.16
    with pytest.raises(ValueError):
        evaluate([100] * 3, False, P)


def test_backtest_round_trip_fills_next_open():
    closes = [100] * 5 + [130, 135, 140, 70, 70, 70]
    r = run_backtest(candles(closes), P, fee=0.0)
    (t,) = r["trades"]
    assert t["entry_price"] == 135 and t["exit_price"] == 70 and not t["open"]
    assert r["stats"]["trades"] == 1 and r["stats"]["win_rate_pct"] == 0
    assert r["stats"]["final_equity"] == pytest.approx(10_000 * 70 / 135)


def test_engine_buys_once_then_sells():
    eng = make_engine([100] * 4 + [130])
    assert eng.run_once()["action"] == "BUY"
    assert eng.run_once()["action"] == "HOLD"          # repeat on same candle: no double buy
    base, quote = eng.broker.balances()
    assert quote == pytest.approx(0) and base == pytest.approx(10_000 * 0.999 / 130)
    assert len(eng.store.orders()) == 1

    eng.market.data.append(candles([60], start_ms=eng.market.data[-1].ts + DAY_MS)[0])
    assert eng.run_once()["action"] == "SELL"
    assert eng.position_qty() == 0
    assert eng.broker.balances()[1] == pytest.approx(10_000 * 0.999 / 130 * 60 * 0.999)
    assert [o["side"] for o in eng.store.orders()] == ["SELL", "BUY"]


def test_engine_respects_allocation_and_pause():
    eng = make_engine([100] * 4 + [130], allocation=0.25)
    eng.set_paused(True)
    assert eng.run_once()["action"] == "PAUSED"
    assert eng.store.orders() == []
    eng.set_paused(False)
    eng.run_once()
    assert eng.broker.balances()[1] == pytest.approx(7_500)


def test_engine_refuses_stale_data():
    eng = make_engine([100] * 4 + [130])
    for c in eng.market.data:
        c.ts -= 10 * DAY_MS
    r = eng.run_once()
    assert not r["ok"] and "stale" in r["error"]
    assert eng.store.orders() == []


def test_closed_only_drops_forming_candle():
    cs = candles([1, 2, 3], start_ms=0)
    assert len(closed_only(cs, now_ms=3 * DAY_MS - 1)) == 2


def test_live_mode_requires_keys_and_password():
    with pytest.raises(ValueError):
        Settings(mode="live").validate()
    with pytest.raises(ValueError):
        Settings(mode="live", api_key="k", api_secret="s").validate()
    Settings(mode="live", api_key="k", api_secret="s", dashboard_password="pw").validate()


def test_api_smoke_and_auth():
    eng = make_engine([100] * 9 + [130], dashboard_password="pw")
    client = TestClient(create_app(eng.s, eng, run_loop=False))
    assert client.get("/api/status").status_code == 401
    client.auth = ("admin", "pw")
    assert client.get("/").status_code == 200
    assert client.post("/api/run").json()["action"] == "BUY"
    st = client.get("/api/status").json()
    assert st["in_position"] and st["mode"] == "paper"
    assert len(client.get("/api/candles").json()) == 10
    assert client.get("/api/backtest").json()["stats"]["trades"] == 0  # signal on last bar has no next open yet
    assert client.post("/api/pause").json() == {"paused": True}
    assert client.get("/api/orders").json()[0]["side"] == "BUY"


def test_live_mode_starts_paused_on_first_launch():
    eng = make_engine([100] * 9 + [130], mode="live", api_key="k", api_secret="s", dashboard_password="pw")
    with TestClient(create_app(eng.s, eng, run_loop=False)) as client:
        client.auth = ("admin", "pw")
        assert client.get("/api/status").json()["paused"] is True
        assert client.post("/api/run").json()["action"] == "PAUSED"
        assert eng.store.orders() == []
