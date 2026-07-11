"""Settings dialog: optional read-only API key + refresh interval (Phase 1 scope)."""

from __future__ import annotations

import keyring.errors
from PyQt6.QtWidgets import QDialog, QFormLayout, QHBoxLayout, QVBoxLayout
from qfluentwidgets import BodyLabel, InfoBar, PasswordLineEdit, PrimaryPushButton, PushButton, SpinBox

from chartpilot.settings.config_store import ConfigStore


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
        form.addRow("Binance API key", self.api_key_edit)
        form.addRow("Binance API secret", self.api_secret_edit)

        notice = BodyLabel(
            "A read-only key only raises rate-limit headroom. Never enter a "
            "key with trading permissions. Public endpoints work without one.",
            self,
        )
        notice.setWordWrap(True)

        clear_button = PushButton("Clear saved key", self)
        clear_button.clicked.connect(self._clear_api_key)

        save_button = PrimaryPushButton("Save", self)
        save_button.clicked.connect(self._save)
        cancel_button = PushButton("Cancel", self)
        cancel_button.clicked.connect(self.reject)

        button_row = QHBoxLayout()
        button_row.addWidget(clear_button)
        button_row.addStretch(1)
        button_row.addWidget(cancel_button)
        button_row.addWidget(save_button)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(notice)
        layout.addLayout(button_row)

    def _clear_api_key(self) -> None:
        self.api_key_edit.clear()
        self.api_secret_edit.clear()
        self.config_store.clear_api_key()

    def _save(self) -> None:
        prefs = self.config_store.load()
        prefs.refresh_interval_seconds = self.refresh_interval_spin.value()
        self.config_store.save(prefs)

        key, secret = self.api_key_edit.text().strip(), self.api_secret_edit.text().strip()
        try:
            if key and secret:
                self.config_store.set_api_key(key, secret)
            elif not key and not secret:
                self.config_store.clear_api_key()
        except keyring.errors.KeyringError:
            InfoBar.error(
                title="Couldn't save API key",
                content="The Windows Credential Locker is unavailable. Refresh interval was saved.",
                parent=self,
            )
            return
        self.accept()
