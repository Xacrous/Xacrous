import time

import pytest
from fastapi.testclient import TestClient

from app.backtest import Candle
from app.broker import ExchangeBroker, PaperBroker
from app.config import Settings
from app.data import DAY_MS
from app.engine import Engine
from app.main import create_app
from app.security import (LoginLimiter, Sessions, TotpVerifier, hash_password, new_totp_secret, totp_now,
                          verify_password)
from app.store import Store
from app.strategy import StrategyParams

P = StrategyParams(sma_length=5)
PW = "correct horse battery staple"
HASH = hash_password(PW)
H = {"X-Requested-With": "btcbot"}


class Market:
    def __init__(self, closes):
        now = int(time.time() * 1000)
        self.data = [Candle(now - (len(closes) + 1 - i) * DAY_MS, c, c, c, c) for i, c in enumerate(closes)]

    def closed(self):
        return self.data

    def price(self):
        return self.data[-1].close


def app_client(totp_secret="", **kw):
    s = Settings(params=P, password_hash=HASH, totp_secret=totp_secret, cookie_secure=False, **kw)
    store = Store(":memory:")
    eng = Engine(s, store, PaperBroker(store, 10_000, 0.001), Market([100] * 9 + [130]))
    return TestClient(create_app(s, eng, run_loop=False)), eng


def login(client, password=PW, code="", user="admin"):
    return client.post("/api/login", json={"username": user, "password": password, "code": code}, headers=H)


# ----- primitives ----------------------------------------------------------------
def test_password_hash_roundtrip():
    assert verify_password(PW, HASH)
    assert not verify_password("wrong", HASH)
    assert not verify_password(PW, "garbage")
    assert hash_password(PW) != HASH  # salted


def test_totp_rfc6238_vector():
    # RFC 6238 test secret "12345678901234567890", T=59 -> 94287082 (8 digits) -> last 6 = 287082
    import base64
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert totp_now(secret, at=59) == "287082"


def test_totp_rejects_replay_and_old_codes():
    secret = new_totp_secret()
    v = TotpVerifier(secret)
    now = time.time()
    code = totp_now(secret, at=now)
    assert v.verify(code, at=now)
    assert not v.verify(code, at=now)              # replay
    assert not v.verify(totp_now(secret, at=now - 120), at=now)
    assert not v.verify("abc123")


def test_sessions_expire_and_revoke():
    s = Sessions(idle_minutes=0.0001)
    t = s.create("admin")
    time.sleep(0.02)
    assert s.get(t) is None
    s = Sessions()
    t = s.create("admin")
    assert s.get(t)["user"] == "admin"
    s.revoke(t)
    assert s.get(t) is None


def test_login_limiter():
    lim = LoginLimiter(per_ip=2, total=3)
    lim.fail("a"); lim.fail("a")
    assert lim.blocked("a") and not lim.blocked("b")
    lim.fail("b")
    assert lim.blocked("c")  # global limit reached


# ----- HTTP ------------------------------------------------------------------------
def test_everything_requires_login():
    client, _ = app_client()
    assert client.get("/api/status").status_code == 401
    assert client.post("/api/resume", headers=H).status_code == 401
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert client.get("/login").status_code == 200
    assert client.get("/healthz").json() == {"ok": True}
    for path in ("/docs", "/openapi.json", "/redoc"):
        assert client.get(path, follow_redirects=False).status_code in (303, 401, 404)


def test_login_logout_flow_and_cookie_flags():
    client, eng = app_client()
    assert login(client, password="nope").status_code == 401
    r = login(client)
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert client.get("/api/status").status_code == 200
    client.post("/api/logout", headers=H)
    assert client.get("/api/status").status_code == 401
    assert any("Failed login" in e["message"] for e in eng.store.events())


def test_secure_cookie_flag_by_default():
    s = Settings(params=P, password_hash=HASH)
    store = Store(":memory:")
    eng = Engine(s, store, PaperBroker(store, 10_000, 0.001), Market([100] * 10))
    client = TestClient(create_app(s, eng, run_loop=False), base_url="https://testserver")
    assert "secure" in login(client).headers["set-cookie"].lower()


def test_two_factor_required_when_configured():
    secret = new_totp_secret()
    client, _ = app_client(totp_secret=secret)
    assert client.get("/api/login-info").json() == {"totp": True}
    assert login(client).status_code == 401
    assert login(client, code="000000").status_code == 401
    assert login(client, code=totp_now(secret)).status_code == 200


def test_bruteforce_lockout():
    client, _ = app_client()
    for _ in range(5):
        assert login(client, password="bad").status_code == 401
    assert login(client).status_code == 429  # even the right password is refused while locked


def test_csrf_header_and_origin_checks():
    client, _ = app_client()
    login(client)
    assert client.post("/api/pause").status_code == 403                       # no custom header
    assert client.post("/api/pause", headers={**H, "Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/pause", headers={**H, "Origin": "http://testserver"}).status_code == 200


def test_security_headers():
    client, _ = app_client()
    r = client.get("/login")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"


# ----- Binance account checks ---------------------------------------------------------
class FakeBinance:
    id = "binance"
    has = {"createMarketBuyOrderWithCost": True}

    def __init__(self, withdrawals=False, trading=True, ip=True):
        self.r = {"enableWithdrawals": withdrawals, "enableSpotAndMarginTrading": trading, "ipRestrict": ip}
        self.orders = []

    def load_markets(self):
        pass

    def market(self, symbol):
        return {"base": "BTC", "quote": "USDT", "limits": {"cost": {"min": 5}}}

    def fetch_balance(self):
        return {"free": {"BTC": 0.0, "USDT": 1000.0}}

    def sapiGetAccountApiRestrictions(self):
        return self.r

    def cost_to_precision(self, symbol, cost):
        return f"{cost:.2f}"

    def create_market_buy_order_with_cost(self, symbol, cost):
        self.orders.append(("buy", cost))
        return {"id": "1", "side": "buy", "status": "closed", "filled": cost / 130, "cost": cost,
                "fee": {"cost": 0.0, "currency": "BNB"}}


def test_verify_rejects_withdrawal_enabled_key():
    info = ExchangeBroker(FakeBinance(withdrawals=True), "BTC/USDT").verify()
    assert not info["ok"] and "WITHDRAW" in info["error"]
    info = ExchangeBroker(FakeBinance(trading=False), "BTC/USDT").verify()
    assert not info["ok"] and "Spot" in info["error"]
    info = ExchangeBroker(FakeBinance(ip=False), "BTC/USDT").verify()
    assert info["ok"] and info["ip_restricted"] is False


def test_engine_will_not_trade_with_unsafe_key():
    s = Settings(params=P, mode="live")
    store = Store(":memory:")
    ex = FakeBinance(withdrawals=True)
    eng = Engine(s, store, ExchangeBroker(ex, "BTC/USDT"), Market([100] * 4 + [130]))
    r = eng.run_once()
    assert not r["ok"] and "WITHDRAW" in r["error"] and ex.orders == []
    ex.r["enableWithdrawals"] = False          # user fixes the key
    store.set("connection", {"ok": False})
    assert eng.run_once()["action"] == "BUY"
    assert ex.orders == [("buy", 1000.0)]
    assert store.orders()[0]["mode"] == "live"


def test_switching_account_resets_position_and_pauses():
    store = Store(":memory:")
    s = Settings(params=P, mode="live", testnet=True)
    eng = Engine(s, store, ExchangeBroker(FakeBinance(), "BTC/USDT", testnet=True), Market([100] * 4 + [130]))
    store.set("paused", False)
    eng.run_once()
    assert eng.position_qty() > 0
    live = Engine(Settings(params=P, mode="live"), store, ExchangeBroker(FakeBinance(), "BTC/USDT"),
                  Market([100] * 4 + [130]))
    assert live.position_qty() == 0 and live.paused
    assert "Account changed" in store.events()[0]["message"]
    same = Engine(Settings(params=P, mode="live"), store, ExchangeBroker(FakeBinance(), "BTC/USDT"),
                  Market([100] * 4 + [130]))
    assert same.paused  # restarting on the same account changes nothing
