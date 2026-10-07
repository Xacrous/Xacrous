"""Wires feed, broker and trader together for the selected symbol and runs them."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from .brokers import BinanceBroker, PaperBroker
from .feed import BinanceFeed, DemoFeed
from .flow import Candles, TradeFlow
from .market import MarketInfo, from_ccxt
from .settings import EnvSettings, SettingsFile, TradeParams
from .store import Store
from .trader import Trader

STEP_S = 0.2
_QUOTES = ("FDUSD", "USDT", "USDC", "TUSD", "BTC", "ETH", "BNB", "EUR", "TRY", "BRL", "JPY")


def _split_symbol(symbol: str) -> tuple[str, str]:
    """Best-effort BASE/QUOTE split, only used for the offline demo feed."""
    for q in _QUOTES:
        if symbol.endswith(q) and len(symbol) > len(q):
            return symbol[: -len(q)], q
    return symbol, "USDT"


def make_exchange(env: EnvSettings, keyed: bool):
    import ccxt.async_support as ccxt
    opts = {"enableRateLimit": True,
            "options": {"defaultType": "spot", "adjustForTimeDifference": True, "recvWindow": 10_000}}
    if keyed:
        opts.update(apiKey=env.api_key, secret=env.api_secret)
    ex = ccxt.binance(opts)
    if env.testnet:
        ex.set_sandbox_mode(True)
    return ex


class Engine:
    def __init__(self, env: EnvSettings, store: Store | None = None):
        self.env = env
        data = Path(env.data_dir)
        self.store = store or Store(str(data / "scalper.db"))
        self.settings = SettingsFile(data / "settings.json")
        self.params = self.settings.load()
        self.trader: Trader | None = None
        self.feed = None
        self.market: MarketInfo | None = None
        self.exchange = None
        self.error: str | None = None
        self._tasks: list[asyncio.Task] = []
        self._balances: tuple[float, tuple] = (0.0, (None, None))
        self._lock = asyncio.Lock()

    @property
    def account(self) -> str:
        if self.env.mode == "paper":
            return "paper"
        return "testnet" if self.env.testnet else "live"

    async def start(self) -> None:
        async with self._lock:
            await self._launch()

    async def stop(self) -> None:
        async with self._lock:
            await self._shutdown()

    async def _launch(self) -> None:
        p = self.params
        self.error = None
        flow, candles = TradeFlow(), Candles()
        try:
            if self.env.feed == "demo":
                base, quote = _split_symbol(p.symbol)
                step = 0.1 if self.env.demo_price < 10 else 0.0001
                self.market = MarketInfo(p.symbol, f"{base}/{quote}", base, quote, self.env.demo_tick, step, step, 5.0)
                self.feed = DemoFeed(flow, candles, start_price=self.env.demo_price, tick=self.env.demo_tick)
            else:
                self.exchange = make_exchange(self.env, keyed=self.env.mode == "live")
                await self.exchange.load_markets()
                self.market = from_ccxt(self.exchange, p.symbol)
                self.feed = BinanceFeed(p.symbol, flow, candles, self.exchange, self.market.ccxt_symbol,
                                        testnet=self.env.testnet and self.env.mode == "live")
            if self.env.mode == "live":
                broker = BinanceBroker(self.exchange, self.market, testnet=self.env.testnet)
                await broker.start()
                if broker.ip_restricted is False:
                    self.store.log("Your API key is not restricted to your IP address. If your home IP is "
                                   "fixed, add it in Binance API Management for extra safety.", "warn")
                poll = 1.0
            else:
                broker = PaperBroker(self.market, p.paper_balance, p.paper_maker_fee, p.paper_taker_fee)
                self.feed.on_book, self.feed.on_trade = broker.on_book, broker.on_trade
                poll = 0.0
            self.trader = Trader(p, self.market, broker, self.store, flow, candles, poll_s=poll)
            await self.trader.start()
        except Exception as exc:  # noqa: BLE001 - shown on the dashboard
            self.error = f"Could not start {p.symbol}: {type(exc).__name__}: {exc}"
            self.store.log(self.error, "error")
            await self._close_exchange()
            self.trader = None
            return
        self._tasks = [asyncio.create_task(self.feed.run()), asyncio.create_task(self._loop())]
        self.store.log(f"Watching {self.market.symbol_id} ({self.market.base}/{self.market.quote}) "
                       f"in {self.account.upper()} mode{' with DEMO data' if self.env.feed == 'demo' else ''}")

    async def _loop(self) -> None:
        while True:
            if self.feed.book.ready:
                await self.trader.step(self.feed.book)
            await asyncio.sleep(STEP_S)

    async def _shutdown(self) -> None:
        if self.trader and self.trader.state == "BUYING":
            # Do not leave a buy working while nobody is watching. A take-profit sell is left in
            # place on purpose: it is a valid exit and the bot picks it up again on restart.
            try:
                await self.trader.broker.cancel(self.trader.buy["id"])
                self.trader._reset_idle(time.time(), cooldown=False)
            except Exception as exc:  # noqa: BLE001
                self.store.log(f"Could not cancel the open buy order on shutdown: {exc}", "error")
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tasks = []
        await self._close_exchange()

    async def _close_exchange(self) -> None:
        if self.exchange:
            try:
                await self.exchange.close()
            except Exception:  # noqa: BLE001
                pass
            self.exchange = None

    # ----- dashboard actions ---------------------------------------------------------------
    async def update_settings(self, new: TradeParams) -> None:
        new.validate()
        async with self._lock:
            if new.symbol != self.params.symbol or new.paper_balance != self.params.paper_balance \
                    or (self.trader is None):
                if self.trader and not self.trader.flat:
                    raise ValueError("Close the open trade before changing the symbol or paper balance")
                self.settings.save(new)
                self.params = new
                await self._shutdown()
                await self._launch()  # the new trader always starts paused
            else:
                self.settings.save(new)
                self.params = new
                self.trader.p = new
                self.trader.signal.p = new.signal
                self.store.log("Settings updated")

    async def balances(self) -> tuple:
        if not self.trader:
            return (None, None)
        now = time.time()
        ttl = 0 if self.env.mode == "paper" else 5
        if now - self._balances[0] > ttl:
            try:
                self._balances = (now, await self.trader.broker.balances())
            except Exception as exc:  # noqa: BLE001
                self.store.log(f"Balance check failed: {exc}", "error")
        return self._balances[1]

    async def status(self) -> dict:
        t, f = self.trader, self.feed
        base, quote = await self.balances()
        book = f.book if f else None
        out = {
            "account": self.account, "demo": self.env.feed == "demo", "error": self.error,
            "settings": self.params.to_dict(),
            "market": self.market.to_dict() if self.market else None,
            "feed": {"connected": bool(f and f.connected), "stale": bool(f and f.stale),
                     "error": f.error if f else None, "messages": f.messages if f else 0},
            "balances": {"base": base, "quote": quote},
            "book": None, "trader": t.status() if t else None, "stats": self.stats(),
        }
        if book and book.ready:
            out["book"] = {"bids": book.bids[:12], "asks": book.asks[:12], "mid": book.mid,
                           "spread_bps": book.spread_bps, "microprice": book.microprice,
                           "last_trade": f.flow.last_price, "candles": [
                               {"t": c.open_time // 1000, "o": c.open, "h": c.high, "l": c.low, "c": c.close}
                               for c in f.candles.items.values()][-90:]}
        return out

    def stats(self) -> dict:
        trades = [x for x in self.store.trades(5000, self.account) if x["symbol"] == self.params.symbol]
        wins = [x for x in trades if x["pnl"] > 0]
        gross_win = sum(x["pnl"] for x in wins)
        gross_loss = -sum(x["pnl"] for x in trades if x["pnl"] <= 0)
        return {"trades": len(trades), "win_rate": len(wins) / len(trades) * 100 if trades else None,
                "pnl": sum(x["pnl"] for x in trades), "fees": sum(x["fees"] for x in trades),
                "avg_pnl_pct": sum(x["pnl_pct"] for x in trades) / len(trades) if trades else None,
                "profit_factor": gross_win / gross_loss if gross_loss else None,
                "avg_hold_s": sum(x["exit_ts"] - x["entry_ts"] for x in trades) / len(trades) if trades else None}
