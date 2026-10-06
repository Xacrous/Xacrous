"""Settings, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .strategy import StrategyParams


def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Settings:
    mode: str = "paper"                 # "paper" or "live"
    exchange: str = "binance"
    symbol: str = "BTC/USDT"
    api_key: str = ""
    api_secret: str = ""
    allocation: float = 1.0             # share of the quote balance used per buy
    paper_start_quote: float = 10_000.0
    fee: float = 0.001
    data_file: str = ""                 # use a CSV/XLSX instead of the exchange (paper only)
    db_path: str = "data/bot.db"
    dashboard_user: str = "admin"
    dashboard_password: str = ""
    check_interval_min: float = 15.0
    params: StrategyParams = field(default_factory=StrategyParams)

    @property
    def base(self) -> str:
        return self.symbol.split("/")[0]

    @property
    def quote(self) -> str:
        return self.symbol.split("/")[1]

    def validate(self) -> None:
        if self.mode not in ("paper", "live"):
            raise ValueError("MODE must be 'paper' or 'live'")
        if not 0 < self.allocation <= 1:
            raise ValueError("ALLOCATION must be between 0 and 1")
        if self.mode == "live":
            if not (self.api_key and self.api_secret):
                raise ValueError("live mode needs API_KEY and API_SECRET")
            if not self.dashboard_password:
                raise ValueError("live mode needs DASHBOARD_PASSWORD so the dashboard is not open to anyone")
            if self.data_file:
                raise ValueError("DATA_FILE can only be used in paper mode")


def load_settings() -> Settings:
    _load_dotenv()
    e = os.environ.get
    s = Settings(
        mode=e("MODE", "paper").lower(),
        exchange=e("EXCHANGE", "binance"),
        symbol=e("SYMBOL", "BTC/USDT"),
        api_key=e("API_KEY", ""),
        api_secret=e("API_SECRET", ""),
        allocation=float(e("ALLOCATION", "1.0")),
        paper_start_quote=float(e("PAPER_START_QUOTE", "10000")),
        fee=float(e("FEE", "0.001")),
        data_file=e("DATA_FILE", ""),
        db_path=e("DB_PATH", "data/bot.db"),
        dashboard_user=e("DASHBOARD_USER", "admin"),
        dashboard_password=e("DASHBOARD_PASSWORD", ""),
        check_interval_min=float(e("CHECK_INTERVAL_MIN", "15")),
        params=StrategyParams(
            sma_length=int(e("SMA_LENGTH", "100")),
            entry_band=float(e("ENTRY_BAND", "0.06")),
            exit_band=float(e("EXIT_BAND", "0.04")),
        ),
    )
    s.validate()
    return s
