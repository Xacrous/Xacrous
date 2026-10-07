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


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    mode: str = "paper"                 # "paper" or "live"
    exchange: str = "binance"
    symbol: str = "BTC/USDT"
    api_key: str = ""
    api_secret: str = ""
    testnet: bool = False               # Binance Spot Testnet: real API, fake money
    allocation: float = 1.0             # share of the quote balance used per buy
    paper_start_quote: float = 10_000.0
    fee: float = 0.001
    data_file: str = ""                 # use a CSV/XLSX instead of the exchange (paper only)
    db_path: str = "data/bot.db"
    check_interval_min: float = 15.0
    params: StrategyParams = field(default_factory=StrategyParams)
    # --- dashboard security ---
    dashboard_user: str = "admin"
    password_hash: str = ""             # from: python -m app.cli set-password
    totp_secret: str = ""               # from: python -m app.cli setup-2fa
    auth_disabled: bool = False         # local development only; refused in live mode
    cookie_secure: bool = True          # set false only when testing over plain http://localhost
    session_idle_min: float = 60
    session_max_hours: float = 12

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
        if not self.auth_disabled and not self.password_hash:
            raise ValueError("DASHBOARD_PASSWORD_HASH is not set. Run: python -m app.cli set-password")
        if self.password_hash and not self.password_hash.startswith("scrypt:"):
            raise ValueError("DASHBOARD_PASSWORD_HASH must come from: python -m app.cli set-password")
        if self.mode == "live":
            if not (self.api_key and self.api_secret):
                raise ValueError("live mode needs API_KEY and API_SECRET")
            if self.auth_disabled:
                raise ValueError("AUTH_DISABLED cannot be used in live mode")
            if not self.totp_secret:
                raise ValueError("live mode needs two-factor login. Run: python -m app.cli setup-2fa")
            if self.data_file:
                raise ValueError("DATA_FILE can only be used in paper mode")
        if self.testnet and self.exchange != "binance":
            raise ValueError("TESTNET is only supported for EXCHANGE=binance")


def load_settings(validate: bool = True) -> Settings:
    _load_dotenv()
    e = os.environ.get
    s = Settings(
        mode=e("MODE", "paper").lower(),
        exchange=e("EXCHANGE", "binance"),
        symbol=e("SYMBOL", "BTC/USDT"),
        api_key=e("API_KEY", ""),
        api_secret=e("API_SECRET", ""),
        testnet=_bool(e("TESTNET")),
        allocation=float(e("ALLOCATION", "1.0")),
        paper_start_quote=float(e("PAPER_START_QUOTE", "10000")),
        fee=float(e("FEE", "0.001")),
        data_file=e("DATA_FILE", ""),
        db_path=e("DB_PATH", "data/bot.db"),
        check_interval_min=float(e("CHECK_INTERVAL_MIN", "15")),
        params=StrategyParams(
            sma_length=int(e("SMA_LENGTH", "100")),
            entry_band=float(e("ENTRY_BAND", "0.06")),
            exit_band=float(e("EXIT_BAND", "0.04")),
        ),
        dashboard_user=e("DASHBOARD_USER", "admin"),
        password_hash=e("DASHBOARD_PASSWORD_HASH", ""),
        totp_secret=e("TOTP_SECRET", ""),
        auth_disabled=_bool(e("AUTH_DISABLED")),
        cookie_secure=_bool(e("COOKIE_SECURE"), True),
        session_idle_min=float(e("SESSION_IDLE_MIN", "60")),
        session_max_hours=float(e("SESSION_MAX_HOURS", "12")),
    )
    if validate:
        s.validate()
    return s
