"""Order execution. PaperBroker simulates fills from the live feed; BinanceBroker sends real orders.

Both return orders in one normalised shape:
    {id, side, type, price, amount, filled, cost, fee_base, fee_quote, status}
status is one of: open, closed (fully filled), canceled, rejected.
fee_base is fee taken out of the coin received on a buy (it reduces what can be sold).
fee_quote is every fee expressed in the quote coin, for profit and loss.
"""
from __future__ import annotations

import itertools
import time

from .book import Book
from .market import MarketInfo

CLIENT_PREFIX = "x-scalp"


class OrderRejected(Exception):
    pass


def _order(id, side, type_, price, amount, filled=0.0, cost=0.0, fee_base=0.0, fee_quote=0.0, status="open"):
    return {"id": str(id), "side": side, "type": type_, "price": price, "amount": amount, "filled": filled,
            "cost": cost, "fee_base": fee_base, "fee_quote": fee_quote, "status": status, "ts": time.time()}


class PaperBroker:
    """Simulated spot account.

    Fill rules are deliberately conservative for limit orders: a resting buy fills only
    when a public trade prints *below* its price (or the ask drops to it); a resting sell
    only when a trade prints *above* it (or the bid rises to it). Being merely at the
    front of the queue is not assumed. Market orders walk the current book.
    """

    mode = "paper"

    def __init__(self, market: MarketInfo, quote_balance: float, maker_fee_pct: float, taker_fee_pct: float):
        self.m = market
        self.base, self.quote = 0.0, quote_balance
        self.maker, self.taker = maker_fee_pct / 100, taker_fee_pct / 100
        self.orders: dict[str, dict] = {}
        self.book = Book()
        self._ids = itertools.count(1)

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def fees(self) -> tuple[float, float]:
        return self.maker, self.taker

    async def balances(self) -> tuple[float, float]:
        return self.base, self.quote

    # ----- feed hooks -------------------------------------------------------------
    def on_book(self, book: Book) -> None:
        self.book = book
        for o in list(self.orders.values()):
            if o["status"] != "open":
                continue
            if o["side"] == "buy" and book.ready and book.best_ask <= o["price"]:
                self._fill_maker(o)
            elif o["side"] == "sell" and book.ready and book.best_bid >= o["price"]:
                self._fill_maker(o)

    def on_trade(self, price: float) -> None:
        for o in list(self.orders.values()):
            if o["status"] != "open":
                continue
            if (o["side"] == "buy" and price < o["price"]) or (o["side"] == "sell" and price > o["price"]):
                self._fill_maker(o)

    # ----- orders ----------------------------------------------------------------------
    async def limit_buy_maker(self, qty: float, price: float) -> dict:
        if self.book.ready and price >= self.book.best_ask:
            raise OrderRejected("post-only buy would have crossed the spread")
        cost = qty * price
        if cost > self.quote + 1e-12:
            raise OrderRejected("insufficient quote balance")
        self.quote -= cost  # reserve
        o = _order(next(self._ids), "buy", "limit", price, qty)
        self.orders[o["id"]] = o
        return dict(o)

    async def limit_sell_maker(self, qty: float, price: float) -> dict:
        if self.book.ready and price <= self.book.best_bid:
            raise OrderRejected("post-only sell would have crossed the spread")
        if qty > self.base + 1e-12:
            raise OrderRejected("insufficient base balance")
        self.base -= qty  # reserve
        o = _order(next(self._ids), "sell", "limit", price, qty)
        self.orders[o["id"]] = o
        return dict(o)

    async def market_buy(self, quote_amount: float) -> dict:
        price = self.book.best_ask
        qty = self.m.qty_down(quote_amount / price)
        cost = qty * price
        if cost > self.quote + 1e-12:
            raise OrderRejected("insufficient quote balance")
        self.quote -= cost
        fee = qty * self.taker
        self.base += qty - fee
        o = _order(next(self._ids), "buy", "market", price, qty, qty, cost, fee, fee * price, "closed")
        self.orders[o["id"]] = o
        return dict(o)

    async def market_sell(self, qty: float) -> dict:
        qty = min(qty, self.base)
        if qty <= 0:
            raise OrderRejected("nothing to sell")
        price = self.book.market_sell_price(qty)
        proceeds = qty * price
        fee = proceeds * self.taker
        self.base -= qty
        self.quote += proceeds - fee
        o = _order(next(self._ids), "sell", "market", price, qty, qty, proceeds, 0.0, fee, "closed")
        self.orders[o["id"]] = o
        return dict(o)

    async def fetch(self, order_id: str) -> dict:
        return dict(self.orders[order_id])

    async def cancel(self, order_id: str) -> dict:
        o = self.orders[order_id]
        if o["status"] == "open":
            o["status"] = "canceled"
            if o["side"] == "buy":
                self.quote += (o["amount"] - o["filled"]) * o["price"]
            else:
                self.base += o["amount"] - o["filled"]
        return dict(o)

    def _fill_maker(self, o: dict) -> None:
        qty, price = o["amount"], o["price"]
        o.update(filled=qty, cost=qty * price, status="closed")
        if o["side"] == "buy":
            fee = qty * self.maker
            o.update(fee_base=fee, fee_quote=fee * price)
            self.base += qty - fee
        else:
            fee = qty * price * self.maker
            o.update(fee_quote=fee)
            self.quote += qty * price - fee


class BinanceBroker:
    """Real orders on Binance spot through ccxt (async)."""

    def __init__(self, exchange, market: MarketInfo, testnet: bool = False):
        self.ex, self.m = exchange, market
        self.mode = "testnet" if testnet else "live"
        self.ip_restricted: bool | None = None
        self._fees: tuple[float, float] | None = None
        self._ids = itertools.count(int(time.time()))

    async def start(self) -> None:
        """Check the API key before any order: it must trade spot and must NOT be able to withdraw."""
        await self.ex.load_markets()
        await self.ex.fetch_balance()  # fails fast on a wrong key, wrong IP or clock problems
        if self.mode == "live":
            r = await self.ex.sapiGetAccountApiRestrictions()
            if str(r.get("enableWithdrawals")).lower() == "true":
                raise PermissionError("this API key can WITHDRAW funds. In Binance API Management turn "
                                      "'Enable Withdrawals' off (or make a new key) before trading")
            if str(r.get("enableSpotAndMarginTrading")).lower() != "true":
                raise PermissionError("this API key cannot trade. Turn on 'Enable Spot & Margin Trading'")
            self.ip_restricted = str(r.get("ipRestrict")).lower() == "true"

    async def close(self) -> None:
        await self.ex.close()

    async def fees(self) -> tuple[float, float]:
        """Your account's real maker/taker fee for this symbol (fractions)."""
        if self._fees is None:
            try:
                f = await self.ex.fetch_trading_fee(self.m.ccxt_symbol)
                self._fees = (float(f["maker"]), float(f["taker"]))
            except Exception:  # noqa: BLE001 - the testnet has no fee endpoint
                self._fees = (0.001, 0.001)
        return self._fees

    async def balances(self) -> tuple[float, float]:
        bal = await self.ex.fetch_balance()
        free = bal.get("free", {})
        return float(free.get(self.m.base) or 0), float(free.get(self.m.quote) or 0)

    def _cid(self) -> str:
        return f"{CLIENT_PREFIX}-{next(self._ids)}"

    async def _create(self, type_, side, qty, price=None, params=None) -> dict:
        import ccxt
        params = {"newClientOrderId": self._cid(), **(params or {})}
        try:
            o = await self.ex.create_order(self.m.ccxt_symbol, type_, side, qty, price, params)
        except ccxt.InvalidOrder as exc:
            raise OrderRejected(str(exc)) from exc
        except ccxt.InsufficientFunds as exc:
            raise OrderRejected(f"insufficient funds: {exc}") from exc
        return await self._normalise(o)

    async def limit_buy_maker(self, qty: float, price: float) -> dict:
        return await self._create("limit", "buy", qty, price, {"postOnly": True})

    async def limit_sell_maker(self, qty: float, price: float) -> dict:
        return await self._create("limit", "sell", qty, price, {"postOnly": True})

    async def market_buy(self, quote_amount: float) -> dict:
        import ccxt
        cost = float(self.ex.cost_to_precision(self.m.ccxt_symbol, quote_amount))
        try:
            o = await self.ex.create_market_buy_order_with_cost(self.m.ccxt_symbol, cost,
                                                                {"newClientOrderId": self._cid()})
        except (ccxt.InvalidOrder, ccxt.InsufficientFunds) as exc:
            raise OrderRejected(str(exc)) from exc
        return await self._normalise(o)

    async def market_sell(self, qty: float) -> dict:
        return await self._create("market", "sell", self.m.qty_down(qty))

    async def fetch(self, order_id: str) -> dict:
        return await self._normalise(await self.ex.fetch_order(order_id, self.m.ccxt_symbol))

    async def cancel(self, order_id: str) -> dict:
        import ccxt
        try:
            await self.ex.cancel_order(order_id, self.m.ccxt_symbol)
        except ccxt.OrderNotFound:
            pass  # already filled or cancelled; fetch tells us which
        return await self.fetch(order_id)

    async def cancel_stray_orders(self, keep: set[str]) -> int:
        """Cancel this bot's open orders (by client id prefix) that it is not tracking."""
        n = 0
        for o in await self.ex.fetch_open_orders(self.m.ccxt_symbol):
            cid = o.get("clientOrderId") or ""
            if cid.startswith(CLIENT_PREFIX) and str(o["id"]) not in keep:
                await self.cancel(str(o["id"]))
                n += 1
        return n

    async def _normalise(self, o: dict) -> dict:
        status = {"open": "open", "closed": "closed", "canceled": "canceled", "expired": "canceled",
                  "rejected": "rejected"}.get(o.get("status"), "open")
        filled = float(o.get("filled") or 0)
        cost = float(o.get("cost") or 0)
        fee_base = fee_quote = 0.0
        if filled > 0 and status != "open":
            fee_base, fee_quote = await self._fees_for(o, filled, cost)
        price = float(o.get("price") or (cost / filled if filled else 0))
        return {**_order(o["id"], o["side"], o.get("type"), price, float(o.get("amount") or filled),
                         filled, cost, fee_base, fee_quote, status)}

    async def _fees_for(self, o: dict, filled: float, cost: float) -> tuple[float, float]:
        """Exact fees from the order's trades; BNB fees are valued at the configured rate."""
        try:
            trades = await self.ex.fetch_my_trades(self.m.ccxt_symbol, params={"orderId": o["id"]})
        except Exception:  # noqa: BLE001
            trades = []
        fee_base = fee_quote = 0.0
        avg = cost / filled if filled else 0
        maker, taker = await self.fees()
        for t in trades:
            fee = t.get("fee") or {}
            amt, cur = float(fee.get("cost") or 0), fee.get("currency")
            if cur == self.m.base:
                fee_base += amt
                fee_quote += amt * avg
            elif cur == self.m.quote:
                fee_quote += amt
            elif amt:  # e.g. BNB: value it at the fee rate applied to the trade value
                fee_quote += float(t.get("cost") or 0) * (maker if t.get("takerOrMaker") == "maker" else taker)
        if not trades:
            fee_quote = cost * (maker if o.get("type") == "limit" else taker)
            if o["side"] == "buy":
                fee_base = fee_quote / avg if avg else 0
        return fee_base, fee_quote
