"""SQLite persistence for bot state, orders and the event log."""
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
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, mode TEXT NOT NULL,
                    side TEXT NOT NULL, qty REAL NOT NULL, price REAL NOT NULL, cost REAL NOT NULL,
                    fee REAL NOT NULL, candle_ts INTEGER, exchange_id TEXT, reason TEXT);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL,
                    level TEXT NOT NULL, message TEXT NOT NULL);
            """)

    def get(self, key: str, default=None):
        with self._lock:
            row = self._db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set(self, key: str, value) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO state(key, value) VALUES(?, ?) "
                             "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    def add_order(self, **o) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO orders(ts, mode, side, qty, price, cost, fee, candle_ts, exchange_id, reason) "
                "VALUES(:ts, :mode, :side, :qty, :price, :cost, :fee, :candle_ts, :exchange_id, :reason)",
                {"exchange_id": None, "reason": None, "candle_ts": None, **o})

    def orders(self, limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def log(self, message: str, level: str = "info") -> None:
        with self._lock, self._db:
            cur = self._db.execute("INSERT INTO events(ts, level, message) VALUES(?, ?, ?)",
                                   (int(time.time() * 1000), level, message[:1000]))
            self._db.execute("DELETE FROM events WHERE id <= ?", (cur.lastrowid - 5000,))  # keep the last 5000

    def events(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
