"""Local dashboard. Only reachable from this PC (127.0.0.1)."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .engine import Engine
from .settings import TradeParams

STATIC = Path(__file__).parent / "static"
HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; "
                               "style-src 'self' 'sha256-3pRED1tOXas1FXFoPb9TGCjmYe9XQsmO9OV23khV2nY='; img-src 'self' data:; "
                               "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def create_app(engine: Engine, start_engine: bool = True) -> FastAPI:
    port = engine.env.port
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}", "127.0.0.1", "localhost",
                     "testserver"}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_engine:
            await engine.start()
        yield
        if start_engine:
            await engine.stop()

    app = FastAPI(title="Binance Scalper", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # Host check blocks "DNS rebinding": a web page on another domain pointing its name at 127.0.0.1.
        if request.headers.get("host", "") not in allowed_hosts:
            return _h(JSONResponse({"detail": "forbidden host"}, status_code=403))
        if request.method == "POST":
            # Other websites open in your browser cannot send this header to localhost without a CORS
            # preflight, which this server never approves.
            origin = request.headers.get("origin")
            if request.headers.get("x-requested-with") != "scalper" or (
                    origin and origin.split("://", 1)[-1] not in allowed_hosts):
                return _h(JSONResponse({"detail": "cross-site request refused"}, status_code=403))
        return _h(await call_next(request))

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/status")
    async def status():
        return await engine.status()

    @app.get("/api/candles")
    async def candles(before: int | None = None):
        try:
            return {"symbol": engine.params.symbol, "candles": await engine.candles(before)}
        except Exception as exc:  # noqa: BLE001 - e.g. network error while scrolling back
            return JSONResponse({"detail": f"could not load older candles: {exc}"}, status_code=502)

    @app.get("/api/trades")
    def trades():
        return engine.store.trades(200, engine.account)

    @app.get("/api/events")
    def events():
        return engine.store.events(150)

    @app.post("/api/start")
    def start():
        if not engine.trader:
            return JSONResponse({"detail": engine.error or "not running"}, status_code=409)
        engine.trader.set_enabled(True)
        return {"ok": True}

    @app.post("/api/pause")
    def pause():
        if engine.trader:
            engine.trader.set_enabled(False)
        return {"ok": True}

    @app.post("/api/close")
    def close_now():
        if engine.trader:
            engine.trader.request_close()
            engine.store.log("Close requested: exiting the open trade at market", "warn")
        return {"ok": True}

    @app.post("/api/settings")
    async def settings(request: Request):
        try:
            new = TradeParams.from_dict(await request.json())
            await engine.update_settings(new)
        except (ValueError, TypeError) as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)
        return {"ok": True}

    return app


def _h(response):
    for k, v in HEADERS.items():
        response.headers.setdefault(k, v)
    return response
