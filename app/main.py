"""FastAPI web app: dashboard, JSON API, login and the background trading loop."""
from __future__ import annotations

import asyncio
import hmac
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .backtest import run_backtest
from .broker import ExchangeBroker, PaperBroker, make_exchange
from .config import Settings, load_settings
from .engine import Engine, ExchangeData, FileData
from .security import LoginLimiter, Sessions, TotpVerifier, verify_password
from .store import Store
from .strategy import StrategyParams, bands

STATIC = Path(__file__).parent / "static"
COOKIE = "btcbot_session"
PUBLIC_PATHS = {"/login", "/api/login", "/api/login-info", "/healthz"}
# The sha256 allows the one <style> tag TradingView Lightweight Charts 4.2.3 injects; nothing else inline runs.
CSP = ("default-src 'self'; script-src 'self'; "
       "style-src 'self' 'sha256-3pRED1tOXas1FXFoPb9TGCjmYe9XQsmO9OV23khV2nY='; img-src 'self' data:; "
       "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
       "frame-ancestors 'none'; form-action 'self'")
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cache-Control": "no-store",
}
# A dummy hash so a wrong username takes as long to reject as a wrong password.
_DUMMY_HASH = "scrypt:32768:8:1:00000000000000000000000000000000:" + "0" * 64


def build_engine(settings: Settings, store: Store) -> Engine:
    if settings.data_file:
        market = FileData(settings.data_file)
    else:  # signals always use real (mainnet) public prices, even when trading on the testnet
        market = ExchangeData(make_exchange(settings.exchange), settings.symbol)
    if settings.mode == "live":
        keyed = make_exchange(settings.exchange, settings.api_key, settings.api_secret, settings.testnet)
        broker = ExchangeBroker(keyed, settings.symbol, testnet=settings.testnet)
    else:
        broker = PaperBroker(store, settings.paper_start_quote, settings.fee)
    return Engine(settings, store, broker, market)


class LoginBody(BaseModel):
    username: str
    password: str
    code: str = ""


def create_app(settings: Settings | None = None, engine: Engine | None = None, run_loop: bool = True) -> FastAPI:
    settings = settings or load_settings()
    if engine is None:
        engine = build_engine(settings, Store(settings.db_path))
    store = engine.store
    sessions = Sessions(settings.session_idle_min, settings.session_max_hours)
    limiter = LoginLimiter()
    totp = TotpVerifier(settings.totp_secret) if settings.totp_secret else None

    async def loop():
        while True:
            await run_in_threadpool(engine.run_once)
            await asyncio.sleep(settings.check_interval_min * 60)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        where = f"{settings.exchange}{' TESTNET' if settings.testnet else ''}"
        store.log(f"Bot started in {settings.mode.upper()} mode on {settings.symbol} @ {where} "
                  f"(SMA{settings.params.sma_length}, +{settings.params.entry_band:.0%}/-{settings.params.exit_band:.0%})")
        if settings.mode == "live" and store.get("paused") is None:
            store.set("paused", True)
            store.log("Live mode starts paused on first launch. Check the dashboard, then press Resume.", "warn")
        if settings.auth_disabled:
            store.log("AUTH_DISABLED is on: anyone who can reach this app can control it.", "warn")
        task = None
        if run_loop:
            await run_in_threadpool(engine.verify_connection)
            task = asyncio.create_task(loop())
        yield
        if task:
            task.cancel()

    app = FastAPI(title="BTC Trend Bot", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            # CSRF: browsers cannot add this header cross-site without a CORS preflight we never allow,
            # and the session cookie is SameSite=Strict on top.
            if request.headers.get("x-requested-with") != "btcbot":
                return _secure(JSONResponse({"detail": "missing request header"}, status_code=403))
            origin = request.headers.get("origin")
            if origin and origin.split("://", 1)[-1] != request.headers.get("host"):
                return _secure(JSONResponse({"detail": "cross-origin request refused"}, status_code=403))
        public = path in PUBLIC_PATHS or path.startswith("/static/")
        if not (public or settings.auth_disabled or sessions.get(request.cookies.get(COOKIE))):
            if path.startswith("/api/"):
                return _secure(JSONResponse({"detail": "login required"}, status_code=401))
            return _secure(RedirectResponse("/login", status_code=303))
        return _secure(await call_next(request))

    bt_cache: dict = {}

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/login", include_in_schema=False)
    def login_page():
        return FileResponse(STATIC / "login.html")

    @app.get("/api/login-info")
    def login_info():
        return {"totp": bool(totp)}

    @app.post("/api/login")
    def login(body: LoginBody, request: Request):
        ip = request.client.host if request.client else "?"
        if limiter.blocked(ip):
            return JSONResponse({"detail": "Too many failed attempts. Try again in 15 minutes."}, status_code=429)
        user_ok = hmac.compare_digest(body.username.encode(), settings.dashboard_user.encode())
        pw_ok = verify_password(body.password, settings.password_hash if user_ok else _DUMMY_HASH)
        code_ok = totp.verify(body.code) if (totp and user_ok and pw_ok) else not totp
        if not (user_ok and pw_ok and code_ok):
            limiter.fail(ip)
            store.log(f"Failed login from {ip}", "warn")
            return JSONResponse({"detail": "Wrong username, password or code."}, status_code=401)
        limiter.reset(ip)
        store.log(f"Login from {ip}")
        resp = JSONResponse({"ok": True})
        resp.set_cookie(COOKIE, sessions.create(settings.dashboard_user), httponly=True,
                        secure=settings.cookie_secure, samesite="strict", path="/",
                        max_age=int(settings.session_max_hours * 3600))
        return resp

    @app.post("/api/logout")
    def logout(request: Request):
        sessions.revoke(request.cookies.get(COOKIE))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/status")
    def get_status():
        return engine.status()

    @app.get("/api/candles")
    def get_candles(limit: int = Query(500, ge=50, le=5000)):
        candles = engine.market.closed()
        lv = bands([c.close for c in candles], settings.params)
        rows = []
        for c, b in list(zip(candles, lv))[-limit:]:
            rows.append({"t": c.ts // 1000, "o": c.open, "h": c.high, "l": c.low, "c": c.close,
                         "sma": b and b[0], "entry": b and b[1], "exit": b and b[2]})
        return rows

    @app.get("/api/backtest")
    def get_backtest(sma_length: int = Query(None, ge=10, le=400), entry_band: float = Query(None, ge=0, le=0.5),
                     exit_band: float = Query(None, ge=0, le=0.5)):
        p = settings.params
        params = StrategyParams(sma_length or p.sma_length,
                                p.entry_band if entry_band is None else entry_band,
                                p.exit_band if exit_band is None else exit_band)
        candles = engine.market.closed()
        key = (params, candles[-1].ts if candles else None, len(candles))
        if key not in bt_cache:
            bt_cache.clear()
            bt_cache[key] = run_backtest(candles, params, fee=settings.fee)
        return bt_cache[key]

    @app.get("/api/orders")
    def get_orders():
        return store.orders()

    @app.get("/api/events")
    def get_events():
        return store.events()

    @app.post("/api/run")
    async def post_run():
        return await run_in_threadpool(engine.run_once)

    @app.post("/api/verify")
    async def post_verify():
        return await run_in_threadpool(engine.verify_connection)

    @app.post("/api/pause")
    def post_pause():
        engine.set_paused(True)
        return {"paused": True}

    @app.post("/api/resume")
    def post_resume():
        engine.set_paused(False)
        return {"paused": False}

    return app


def _secure(response):
    for k, v in SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    return response


def app_factory() -> FastAPI:  # used by: uvicorn app.main:app_factory --factory
    return create_app()
