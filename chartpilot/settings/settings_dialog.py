"""Settings dialog: refresh interval, theme, optional read-only API key, and
the separate trading-permission API key used by the Trade tab's auto-trader."""

from __future__ import annotations

import keyring.errors
from PyQt6.QtWidgets import QDialog, QFormLayout, QHBoxLayout, QVBoxLayout
from qfluentwidgets import (
    BodyLabel,
    CheckBox,
    ComboBox,
    InfoBar,
    PasswordLineEdit,
    PrimaryPushButton,
    PushButton,
    SpinBox,
    StrongBodyLabel,
    Theme,
    setTheme,
)

from chartpilot.settings.config_store import ConfigStore

_THEMES = ["dark", "light"]


class SettingsDialog(QDialog):
    def __init__(self, config_store: ConfigStore, parent=None) -> None:
        super().__init__(parent)
        self.config_store = config_store
        self.setWindowTitle("Settings")
        self.setMinimumWidth(420)

        prefs = self.config_store.load()

        self.refresh_interval_spin = SpinBox(self)
        self.refresh_interval_spin.setRange(5, 3600)
        self.refresh_interval_spin.setSuffix(" s")
        self.refresh_interval_spin.setValue(prefs.refresh_interval_seconds)

        self.theme_combo = ComboBox(self)
        self.theme_combo.addItems([t.capitalize() for t in _THEMES])
        if prefs.theme in _THEMES:
            self.theme_combo.setCurrentIndex(_THEMES.index(prefs.theme))

        self.api_key_edit = PasswordLineEdit(self)
        self.api_key_edit.setPlaceholderText("Optional — read-only key only")
        self.api_secret_edit = PasswordLineEdit(self)
        self.api_secret_edit.setPlaceholderText("Optional — read-only secret only")

        existing = self.config_store.get_api_key()
        if existing is not None:
            self.api_key_edit.setText(existing[0])
            self.api_secret_edit.setText(existing[1])

        form = QFormLayout()
        form.addRow("Refresh interval", self.refresh_interval_spin)
        form.addRow("Theme", self.theme_combo)
        form.addRow("Binance API key", self.api_key_edit)
        form.addRow("Binance API secret", self.api_secret_edit)

        notice = BodyLabel(
            "A read-only key only raises rate-limit headroom. Never enter a "
            "key with trading permissions here. Public endpoints work without one.",
            self,
        )
        notice.setWordWrap(True)

        clear_button = PushButton("Clear saved key", self)
        clear_button.clicked.connect(self._clear_api_key)

        # Auto-Trade section: a deliberately separate, clearly-labeled
        # credential slot — this key DOES need trading permissions, since
        # it's what the Trade tab's auto-trader uses to place real orders.
        trade_heading = StrongBodyLabel("Auto-Trade API Key (trading-enabled)", self)

        trade_warning = BodyLabel(
            "⚠ This key places real buy/sell orders on your Binance account when the "
            "Trade tab's auto-trader is running. Only enter a key scoped to Spot "
            "Trading — never enable withdrawals. Leave \"Use Binance Testnet\" checked "
            "until you've verified the bot's behavior with fake funds.",
            self,
        )
        trade_warning.setWordWrap(True)

        self.testnet_check = CheckBox("Use Binance Testnet (fake funds, recommended)", self)
        self.testnet_check.setChecked(prefs.auto_trade_use_testnet)

        self.trading_api_key_edit = PasswordLineEdit(self)
        self.trading_api_key_edit.setPlaceholderText("Optional — required to run the Trade tab's auto-trader")
        self.trading_api_secret_edit = PasswordLineEdit(self)
        self.trading_api_secret_edit.setPlaceholderText("Optional — trading key's secret")

        existing_trading = self.config_store.get_trading_api_key()
        if existing_trading is not None:
            self.trading_api_key_edit.setText(existing_trading[0])
            self.trading_api_secret_edit.setText(existing_trading[1])

        trade_form = QFormLayout()
        trade_form.addRow("Use testnet", self.testnet_check)
        trade_form.addRow("Trading API key", self.trading_api_key_edit)
        trade_form.addRow("Trading API secret", self.trading_api_secret_edit)

        clear_trading_button = PushButton("Clear saved trading key", self)
        clear_trading_button.clicked.connect(self._clear_trading_api_key)

        save_button = PrimaryPushButton("Save", self)
        save_button.clicked.connect(self._save)
        cancel_button = PushButton("Cancel", self)
        cancel_button.clicked.connect(self.reject)

        button_row = QHBoxLayout()
        button_row.addWidget(clear_button)
        button_row.addWidget(clear_trading_button)
        button_row.addStretch(1)
        button_row.addWidget(cancel_button)
        button_row.addWidget(save_button)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(notice)
        layout.addWidget(trade_heading)
        layout.addWidget(trade_warning)
        layout.addLayout(trade_form)
        layout.addLayout(button_row)

    def _clear_api_key(self) -> None:
        self.api_key_edit.clear()
        self.api_secret_edit.clear()
        self.config_store.clear_api_key()

    def _clear_trading_api_key(self) -> None:
        self.trading_api_key_edit.clear()
        self.trading_api_secret_edit.clear()
        self.config_store.clear_trading_api_key()

    def _save(self) -> None:
        prefs = self.config_store.load()
        prefs.refresh_interval_seconds = self.refresh_interval_spin.value()
        prefs.theme = _THEMES[self.theme_combo.currentIndex()]
        prefs.auto_trade_use_testnet = self.testnet_check.isChecked()
        self.config_store.save(prefs)
        setTheme(Theme.DARK if prefs.theme == "dark" else Theme.LIGHT)

        key, secret = self.api_key_edit.text().strip(), self.api_secret_edit.text().strip()
        trading_key, trading_secret = self.trading_api_key_edit.text().strip(), self.trading_api_secret_edit.text().strip()
        try:
            if key and secret:
                self.config_store.set_api_key(key, secret)
            elif not key and not secret:
                self.config_store.clear_api_key()
            if trading_key and trading_secret:
                self.config_store.set_trading_api_key(trading_key, trading_secret)
            elif not trading_key and not trading_secret:
                self.config_store.clear_trading_api_key()
        except keyring.errors.KeyringError:
            InfoBar.error(
                title="Couldn't save API key",
                content="The Windows Credential Locker is unavailable. Other settings were saved.",
                parent=self,
            )
            return
        self.accept()
