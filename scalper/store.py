"""SQLite: closed trades, the activity log and small pieces of state."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path


class Store:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, mode TEXT, symbol TEXT,
                    entry_ts REAL, exit_ts REAL, qty REAL, entry_price REAL, exit_price REAL,
                    fees REAL, pnl REAL, pnl_pct REAL, reason TEXT);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, level TEXT, message TEXT);
            """)

    def get(self, key, default=None):
        with self._lock:
            row = self._db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set(self, key, value) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO state VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                             (key, json.dumps(value)))

    def add_trade(self, **t) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO trades(mode, symbol, entry_ts, exit_ts, qty, entry_price, exit_price, "
                             "fees, pnl, pnl_pct, reason) VALUES(:mode, :symbol, :entry_ts, :exit_ts, :qty, "
                             ":entry_price, :exit_price, :fees, :pnl, :pnl_pct, :reason)", t)

    def trades(self, limit: int = 200, mode: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM trades", []
        if mode:
            q, args = q + " WHERE mode=?", [mode]
        with self._lock:
            rows = self._db.execute(q + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
        return [dict(r) for r in rows]

    def trades_since(self, ts: float, mode: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM trades WHERE exit_ts >= ? AND mode=? ORDER BY id",
                                    (ts, mode)).fetchall()
        return [dict(r) for r in rows]

    def log(self, message: str, level: str = "info") -> None:
        with self._lock, self._db:
            cur = self._db.execute("INSERT INTO events(ts, level, message) VALUES(?, ?, ?)",
                                   (time.time(), level, message[:1000]))
            self._db.execute("DELETE FROM events WHERE id <= ?", (cur.lastrowid - 5000,))

    def events(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
