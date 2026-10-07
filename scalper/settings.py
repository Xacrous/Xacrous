"""Settings: secrets and mode from .env, trading settings from data/settings.json (editable in the dashboard)."""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .signals import SignalParams


def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _bool(v: str | None, default=False) -> bool:
    return default if v in (None, "") else v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class EnvSettings:
    mode: str = "paper"            # paper = simulated fills on live data, live = real orders
    testnet: bool = False          # live orders on the Binance Spot Testnet (fake money)
    api_key: str = ""
    api_secret: str = ""
    host: str = "127.0.0.1"        # dashboard address; keep it on localhost
    port: int = 8080
    data_dir: str = "data"
    feed: str = "binance"          # "demo" = synthetic market for trying the dashboard offline

    def validate(self) -> None:
        if self.mode not in ("paper", "live"):
            raise ValueError("MODE must be paper or live")
        if self.mode == "live" and not (self.api_key and self.api_secret):
            raise ValueError("MODE=live needs API_KEY and API_SECRET in .env")
        if self.mode == "live" and self.feed != "binance":
            raise ValueError("FEED=demo can only be used with MODE=paper")
        if self.host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("HOST must be 127.0.0.1: the dashboard has no login and must not be "
                             "reachable from other computers")


def load_env() -> EnvSettings:
    _load_dotenv()
    e = os.environ.get
    s = EnvSettings(mode=e("MODE", "paper").lower(), testnet=_bool(e("TESTNET")), api_key=e("API_KEY", ""),
                    api_secret=e("API_SECRET", ""), host=e("HOST", "127.0.0.1"), port=int(e("PORT", "8080")),
                    data_dir=e("DATA_DIR", "data"), feed=e("FEED", "binance").lower())
    s.validate()
    return s


SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,20}$")


@dataclass
class TradeParams:
    symbol: str = "BTCUSDT"
    order_quote: float = 20.0          # size of each buy, in the quote coin (USDT for BTCUSDT, BTC for ETHBTC)
    min_profit_pct: float = 0.12       # take-profit, NET of both fees
    stop_loss_pct: float = 0.30        # exit at market if price falls this far below entry
    max_hold_s: float = 300            # exit at market after this long (5 x 1m candles)
    entry_timeout_s: float = 15        # cancel an unfilled buy after this long
    cooldown_s: float = 10             # pause between trades
    use_maker_entry: bool = True       # post-only limit buy at the bid (cheaper fee) vs market buy
    max_trades_per_hour: int = 30
    max_daily_loss: float = 2.0        # quote coin; trading stops for the day after this loss
    max_consecutive_losses: int = 4    # trading pauses after this many losses in a row
    paper_balance: float = 1000.0      # starting quote balance for paper trading
    paper_maker_fee: float = 0.10      # % (Binance VIP0; 0.075 with BNB discount)
    paper_taker_fee: float = 0.10      # %
    signal: SignalParams = field(default_factory=SignalParams)

    def validate(self) -> None:
        if not SYMBOL_RE.match(self.symbol):
            raise ValueError("symbol must look like BTCUSDT or ETHBTC")
        checks = [
            (self.order_quote > 0, "order size must be > 0"),
            (self.min_profit_pct >= 0.12, "minimum profit must be at least 0.12%"),
            (0.05 <= self.stop_loss_pct <= 5, "stop loss must be between 0.05% and 5%"),
            (10 <= self.max_hold_s <= 3600, "max hold must be 10-3600 s"),
            (2 <= self.entry_timeout_s <= 300, "entry timeout must be 2-300 s"),
            (self.cooldown_s >= 0, "cooldown must be >= 0"),
            (1 <= self.max_trades_per_hour <= 600, "max trades per hour must be 1-600"),
            (self.max_daily_loss > 0, "max daily loss must be > 0"),
            (1 <= self.max_consecutive_losses <= 100, "max consecutive losses must be 1-100"),
            (0 <= self.paper_maker_fee <= 1 and 0 <= self.paper_taker_fee <= 1, "fees must be 0-1%"),
            (0 < self.signal.entry_score <= 1, "entry score must be 0-1"),
            (-1 <= self.signal.exit_score < 0, "exit score must be -1-0"),
        ]
        for ok, msg in checks:
            if not ok:
                raise ValueError(msg)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TradeParams":
        d = dict(d)
        sig = SignalParams(**{f.name: d.get("signal", {}).get(f.name, getattr(SignalParams(), f.name))
                              for f in fields(SignalParams)})
        d.pop("signal", None)
        known = {f.name for f in fields(cls)} - {"signal"}
        p = cls(**{k: v for k, v in d.items() if k in known}, signal=sig)
        p.symbol = p.symbol.strip().upper().replace("/", "").replace("-", "")
        return p


class SettingsFile:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> TradeParams:
        if self.path.exists():
            return TradeParams.from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        return TradeParams()

    def save(self, p: TradeParams) -> None:
        p.validate()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(p.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(self.path)
