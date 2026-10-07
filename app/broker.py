"""Order execution: a paper broker for simulation and a ccxt broker for real money."""
from __future__ import annotations

import time

from .store import Store


class OrderError(RuntimeError):
    pass


def make_exchange(exchange_id: str, api_key: str = "", api_secret: str = "", testnet: bool = False):
    """Build a ccxt spot client. Without keys it can only read public market data."""
    import ccxt
    opts = {"enableRateLimit": True,
            "options": {"defaultType": "spot", "adjustForTimeDifference": True, "recvWindow": 10_000}}
    if api_key:
        opts.update(apiKey=api_key, secret=api_secret)
    ex = getattr(ccxt, exchange_id)(opts)
    if testnet:
        ex.set_sandbox_mode(True)
    return ex


class PaperBroker:
    """Simulated spot account kept in the database. Fills at the price it is given."""

    mode = "paper"

    def verify(self) -> dict:
        return {"ok": True, "checked": int(time.time() * 1000), "account": "paper"}

    def __init__(self, store: Store, start_quote: float, fee: float):
        self.store, self.fee = store, fee
        if store.get("paper_quote") is None:
            store.set("paper_quote", start_quote)
            store.set("paper_base", 0.0)

    def balances(self) -> tuple[float, float]:
        return float(self.store.get("paper_base", 0.0)), float(self.store.get("paper_quote", 0.0))

    def market_buy(self, quote_amount: float, price: float) -> dict:
        base, quote = self.balances()
        quote_amount = min(quote_amount, quote)
        if quote_amount <= 0:
            raise OrderError("no quote balance to buy with")
        qty = quote_amount * (1 - self.fee) / price
        self.store.set("paper_quote", quote - quote_amount)
        self.store.set("paper_base", base + qty)
        return {"qty": qty, "price": price, "cost": quote_amount, "fee": quote_amount * self.fee, "id": None}

    def market_sell(self, qty: float, price: float) -> dict:
        base, quote = self.balances()
        qty = min(qty, base)
        if qty <= 0:
            raise OrderError("no base balance to sell")
        proceeds = qty * price
        self.store.set("paper_base", base - qty)
        self.store.set("paper_quote", quote + proceeds * (1 - self.fee))
        return {"qty": qty, "price": price, "cost": proceeds, "fee": proceeds * self.fee, "id": None}


class ExchangeBroker:
    """Real spot market orders through ccxt (Binance by default)."""

    def __init__(self, exchange, symbol: str, testnet: bool = False):
        self.ex, self.symbol, self.testnet = exchange, symbol, testnet
        self.mode = "testnet" if testnet else "live"
        self._market = None

    @property
    def market(self) -> dict:
        if self._market is None:  # loaded lazily so a network blip at startup is not fatal
            self.ex.load_markets()
            self._market = self.ex.market(self.symbol)
        return self._market

    def verify(self) -> dict:
        """Check the API key works, can trade spot, and cannot withdraw."""
        info = {"ok": False, "checked": int(time.time() * 1000), "account": self.mode,
                "can_trade": None, "can_withdraw": None, "ip_restricted": None, "error": None}
        try:
            self.market  # noqa: B018 - loads markets
            self.balances()
            if self.ex.id == "binance" and not self.testnet:
                r = self.ex.sapiGetAccountApiRestrictions()
                info["can_trade"] = _truthy(r.get("enableSpotAndMarginTrading"))
                info["can_withdraw"] = _truthy(r.get("enableWithdrawals"))
                info["ip_restricted"] = _truthy(r.get("ipRestrict"))
                if info["can_withdraw"]:
                    raise OrderError("this API key can WITHDRAW funds. Create a key with withdrawals "
                                     "disabled before trading")
                if not info["can_trade"]:
                    raise OrderError("this API key does not have 'Enable Spot & Margin Trading' turned on")
            info["ok"] = True
        except Exception as exc:  # noqa: BLE001 - reported to the dashboard
            info["error"] = f"{type(exc).__name__}: {exc}"
        return info

    def balances(self) -> tuple[float, float]:
        bal = self.ex.fetch_balance()
        free = bal.get("free", {})
        return float(free.get(self.market["base"]) or 0), float(free.get(self.market["quote"]) or 0)

    def _min_cost(self) -> float:
        return float((self.market.get("limits", {}).get("cost") or {}).get("min") or 0)

    def market_buy(self, quote_amount: float, price: float) -> dict:
        if quote_amount < self._min_cost():
            raise OrderError(f"buy of {quote_amount:.2f} is below the exchange minimum {self._min_cost()}")
        if self.ex.has.get("createMarketBuyOrderWithCost"):
            order = self.ex.create_market_buy_order_with_cost(self.symbol, float(self.ex.cost_to_precision(self.symbol, quote_amount)))
        else:
            amount = float(self.ex.amount_to_precision(self.symbol, quote_amount / price))
            order = self.ex.create_market_buy_order(self.symbol, amount)
        return self._fill(order)

    def market_sell(self, qty: float, price: float) -> dict:
        amount = float(self.ex.amount_to_precision(self.symbol, qty))
        if amount <= 0 or amount * price < self._min_cost():
            raise OrderError(f"sell of {qty} {self.market['base']} is below the exchange minimum")
        return self._fill(self.ex.create_market_sell_order(self.symbol, amount))

    def _fill(self, order: dict) -> dict:
        # Market orders usually come back filled; poll briefly if not.
        for _ in range(10):
            if order.get("status") == "closed" and order.get("filled"):
                break
            time.sleep(1)
            order = self.ex.fetch_order(order["id"], self.symbol)
        filled = float(order.get("filled") or 0)
        if filled <= 0:
            raise OrderError(f"order {order.get('id')} not filled (status {order.get('status')})")
        cost = float(order.get("cost") or filled * float(order.get("average") or order.get("price") or 0))
        fee = order.get("fee") or {}
        fee_cost = float(fee.get("cost") or 0)
        # Binance may charge the fee in the base coin on buys; we hold what we actually received.
        qty = filled - fee_cost if order["side"] == "buy" and fee.get("currency") == self.market["base"] else filled
        return {"qty": qty, "price": cost / filled, "cost": cost, "fee": fee_cost, "id": str(order["id"])}


def _truthy(value) -> bool:
    return value is True or str(value).lower() == "true"
