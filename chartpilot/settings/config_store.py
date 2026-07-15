"""JSON preferences + keyring-backed optional read-only API key storage.

The API key (if the user opts to supply one) is handed to the OS credential
store via `keyring` and never touches the plaintext preferences file
(Section 2.1's rationale for choosing `keyring`).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import keyring

_KEYRING_SERVICE = "ChartPilot"
_KEYRING_API_KEY_USERNAME = "binance_api_key"
_KEYRING_API_SECRET_USERNAME = "binance_api_secret"
# Deliberately separate credential slot from the read-only analysis key
# above: this one needs trading permissions to place real orders for the
# Trade tab's auto-trader, so it's kept out of the read-only key path
# entirely rather than letting one key silently gain a second purpose.
_KEYRING_TRADING_API_KEY_USERNAME = "binance_trading_api_key"
_KEYRING_TRADING_API_SECRET_USERNAME = "binance_trading_api_secret"


def default_config_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "ChartPilot"
    return Path.home() / ".chartpilot"


@dataclass
class Preferences:
    symbol: str = "BTCUSDT"
    timeframe: str = "4h"
    mode: str = "swing"
    strategy: str = "trend_following_ma_cross"
    refresh_interval_seconds: int = 30
    theme: str = "dark"
    disclaimer_acknowledged: bool = False
    # Auto-trade (Trade tab) settings — default to testnet so a fresh
    # install can never place a real order without the user explicitly
    # opting in via Settings.
    auto_trade_use_testnet: bool = True
    auto_trade_amount_usdt: float = 50.0
    auto_trade_profit_pct: float = 2.0
    auto_trade_loss_pct: float = 1.0


class ConfigStore:
    def __init__(self, config_dir: str | Path | None = None) -> None:
        self.config_dir = Path(config_dir) if config_dir is not None else default_config_dir()
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.config_dir / "preferences.json"

    def load(self) -> Preferences:
        if not self.config_path.exists():
            return Preferences()
        try:
            data = json.loads(self.config_path.read_text())
        except (json.JSONDecodeError, OSError):
            return Preferences()
        defaults = asdict(Preferences())
        defaults.update({k: v for k, v in data.items() if k in defaults})
        return Preferences(**defaults)

    def save(self, preferences: Preferences) -> None:
        self.config_path.write_text(json.dumps(asdict(preferences), indent=2))

    def set_api_key(self, api_key: str, api_secret: str) -> None:
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_API_KEY_USERNAME, api_key)
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_API_SECRET_USERNAME, api_secret)

    def get_api_key(self) -> tuple[str, str] | None:
        try:
            api_key = keyring.get_password(_KEYRING_SERVICE, _KEYRING_API_KEY_USERNAME)
            api_secret = keyring.get_password(_KEYRING_SERVICE, _KEYRING_API_SECRET_USERNAME)
        except keyring.errors.KeyringError:
            return None
        if not api_key or not api_secret:
            return None
        return api_key, api_secret

    def clear_api_key(self) -> None:
        for username in (_KEYRING_API_KEY_USERNAME, _KEYRING_API_SECRET_USERNAME):
            try:
                keyring.delete_password(_KEYRING_SERVICE, username)
            except keyring.errors.KeyringError:
                pass

    def set_trading_api_key(self, api_key: str, api_secret: str) -> None:
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_TRADING_API_KEY_USERNAME, api_key)
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_TRADING_API_SECRET_USERNAME, api_secret)

    def get_trading_api_key(self) -> tuple[str, str] | None:
        try:
            api_key = keyring.get_password(_KEYRING_SERVICE, _KEYRING_TRADING_API_KEY_USERNAME)
            api_secret = keyring.get_password(_KEYRING_SERVICE, _KEYRING_TRADING_API_SECRET_USERNAME)
        except keyring.errors.KeyringError:
            return None
        if not api_key or not api_secret:
            return None
        return api_key, api_secret

    def clear_trading_api_key(self) -> None:
        for username in (_KEYRING_TRADING_API_KEY_USERNAME, _KEYRING_TRADING_API_SECRET_USERNAME):
            try:
                keyring.delete_password(_KEYRING_SERVICE, username)
            except keyring.errors.KeyringError:
                pass
