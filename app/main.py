"""FastAPI web app: dashboard, JSON API and the background trading loop."""
from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

from .backtest import run_backtest
from .broker import ExchangeBroker, PaperBroker
from .config import Settings, load_settings
from .engine import Engine, ExchangeData, FileData
from .store import Store
from .strategy import StrategyParams, bands

STATIC = Path(__file__).parent / "static"


def build_engine(settings: Settings, store: Store) -> Engine:
    exchange = None
    if not settings.data_file or settings.mode == "live":
        import ccxt
        opts = {"enableRateLimit": True}
        if settings.mode == "live":
            opts.update(apiKey=settings.api_key, secret=settings.api_secret)
        exchange = getattr(ccxt, settings.exchange)(opts)
    market = FileData(settings.data_file) if settings.data_file else ExchangeData(exchange, settings.symbol)
    if settings.mode == "live":
        broker = ExchangeBroker(exchange, settings.symbol)
    else:
        broker = PaperBroker(store, settings.paper_start_quote, settings.fee)
    return Engine(settings, store, broker, market)


def create_app(settings: Settings | None = None, engine: Engine | None = None, run_loop: bool = True) -> FastAPI:
    settings = settings or load_settings()
    if engine is None:
        store = Store(settings.db_path)
        engine = build_engine(settings, store)
    store = engine.store

    async def loop():
        while True:
            await run_in_threadpool(engine.run_once)
            await asyncio.sleep(settings.check_interval_min * 60)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store.log(f"Bot started in {settings.mode.upper()} mode on {settings.symbol} "
                  f"(SMA{settings.params.sma_length}, +{settings.params.entry_band:.0%}/-{settings.params.exit_band:.0%})")
        if settings.mode == "live" and store.get("paused") is None:
            store.set("paused", True)
            store.log("Live mode starts paused on first launch. Check the dashboard, then press Resume.", "warn")
        task = asyncio.create_task(loop()) if run_loop else None
        yield
        if task:
            task.cancel()

    security = HTTPBasic(auto_error=False)

    def auth(creds: HTTPBasicCredentials | None = Depends(security)):
        if not settings.dashboard_password:
            return
        ok = creds and secrets.compare_digest(creds.username, settings.dashboard_user) \
            and secrets.compare_digest(creds.password, settings.dashboard_password)
        if not ok:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, headers={"WWW-Authenticate": "Basic"})

    app = FastAPI(title="BTC Trend Bot", lifespan=lifespan, dependencies=[Depends(auth)])
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    bt_cache: dict = {}

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

    @app.post("/api/pause")
    def post_pause():
        engine.set_paused(True)
        return {"paused": True}

    @app.post("/api/resume")
    def post_resume():
        engine.set_paused(False)
        return {"paused": False}

    return app


def app_factory() -> FastAPI:  # used by: uvicorn app.main:app_factory --factory
    return create_app()
