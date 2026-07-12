"""SQLite-backed candle cache with TTL logic.

A symbol/timeframe combo the user just looked at should not re-fetch and
re-download candles from Binance if the cache is still fresh — `get()`
returns `None` when there is no cached data or when it has aged past the
caller-supplied TTL, which is the only signal `ExchangeClient` needs to
decide whether to hit the network.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pandas as pd

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    open_time INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    PRIMARY KEY (symbol, timeframe, open_time)
);

CREATE TABLE IF NOT EXISTS fetch_meta (
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    last_fetched REAL NOT NULL,
    PRIMARY KEY (symbol, timeframe)
);
"""

CANDLE_COLUMNS = ["open_time", "open", "high", "low", "close", "volume"]


class CandleCache:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def is_stale(self, symbol: str, timeframe: str, ttl_seconds: float) -> bool:
        row = self._conn.execute(
            "SELECT last_fetched FROM fetch_meta WHERE symbol = ? AND timeframe = ?",
            (symbol, timeframe),
        ).fetchone()
        if row is None:
            return True
        return (time.time() - row[0]) > ttl_seconds

    def get(self, symbol: str, timeframe: str, limit: int, ttl_seconds: float) -> pd.DataFrame | None:
        """Return up to `limit` cached candles if fresh, else None."""
        if self.is_stale(symbol, timeframe, ttl_seconds):
            return None
        rows = self._conn.execute(
            """
            SELECT open_time, open, high, low, close, volume
            FROM candles
            WHERE symbol = ? AND timeframe = ?
            ORDER BY open_time DESC
            LIMIT ?
            """,
            (symbol, timeframe, limit),
        ).fetchall()
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=CANDLE_COLUMNS).iloc[::-1].reset_index(drop=True)
        return df

    def get_before(self, symbol: str, timeframe: str, before_open_time_ms: int, limit: int) -> pd.DataFrame | None:
        """Return up to `limit` cached candles strictly older than
        `before_open_time_ms`, oldest to newest. No TTL check: historical
        candles (unlike the most recent one) never go stale once closed."""
        rows = self._conn.execute(
            """
            SELECT open_time, open, high, low, close, volume
            FROM candles
            WHERE symbol = ? AND timeframe = ? AND open_time < ?
            ORDER BY open_time DESC
            LIMIT ?
            """,
            (symbol, timeframe, before_open_time_ms, limit),
        ).fetchall()
        if not rows:
            return None
        return pd.DataFrame(rows, columns=CANDLE_COLUMNS).iloc[::-1].reset_index(drop=True)

    def upsert(self, symbol: str, timeframe: str, df: pd.DataFrame) -> None:
        records = [
            (symbol, timeframe, int(r.open_time), float(r.open), float(r.high), float(r.low), float(r.close), float(r.volume))
            for r in df.itertuples(index=False)
        ]
        self._conn.executemany(
            """
            INSERT INTO candles (symbol, timeframe, open_time, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, timeframe, open_time) DO UPDATE SET
                open=excluded.open, high=excluded.high, low=excluded.low,
                close=excluded.close, volume=excluded.volume
            """,
            records,
        )
        self._conn.execute(
            """
            INSERT INTO fetch_meta (symbol, timeframe, last_fetched)
            VALUES (?, ?, ?)
            ON CONFLICT(symbol, timeframe) DO UPDATE SET last_fetched=excluded.last_fetched
            """,
            (symbol, timeframe, time.time()),
        )
        self._conn.commit()
