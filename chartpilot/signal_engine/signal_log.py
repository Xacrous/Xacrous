"""SQLite-backed forward signal log.

Every real signal ChartPilot generates gets timestamped here and later
auto-resolved as TP-hit / SL-hit / expired once new candle data catches up
— so the tool is graded on its own live calls, not just curve-fit history
(Section 3's confidence-vs-win-rate distinction).
"""

from __future__ import annotations

import csv
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from chartpilot.signal_engine.base_strategy import Signal
from chartpilot.signal_engine.resolution import resolve_signal

_SCHEMA = """
CREATE TABLE IF NOT EXISTS signal_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    mode TEXT NOT NULL,
    strategy TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry REAL NOT NULL,
    take_profit REAL NOT NULL,
    stop_loss REAL NOT NULL,
    confidence INTEGER NOT NULL,
    reward_risk_ratio REAL NOT NULL,
    generated_at TEXT NOT NULL,
    generated_at_ms INTEGER NOT NULL,
    expires_at TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    resolved_at TEXT
);
"""

_LIST_COLUMNS = (
    "symbol", "timeframe", "mode", "strategy", "direction", "entry", "take_profit", "stop_loss",
    "confidence", "reward_risk_ratio", "generated_at", "expires_at", "status", "resolved_at",
)


def _iso_to_ms(iso_timestamp: str) -> int:
    return int(datetime.fromisoformat(iso_timestamp).timestamp() * 1000)


def _ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


class SignalLog:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._lock = threading.Lock()

    def close(self) -> None:
        self._conn.close()

    def record_if_new(self, signal: Signal) -> int | None:
        """Insert a pending row unless one is already pending for this exact
        symbol/timeframe/mode/strategy/direction combo — a persisting trend
        setup shouldn't count as a fresh trade on every refresh. Returns the
        new row id, or None if a matching pending signal was already logged.
        """
        with self._lock:
            existing = self._conn.execute(
                """
                SELECT id FROM signal_log
                WHERE symbol = ? AND timeframe = ? AND mode = ? AND strategy = ? AND direction = ? AND status = 'pending'
                """,
                (signal.symbol, signal.timeframe, signal.mode, signal.strategy, signal.direction),
            ).fetchone()
            if existing is not None:
                return None
            cursor = self._conn.execute(
                """
                INSERT INTO signal_log (symbol, timeframe, mode, strategy, direction, entry, take_profit,
                    stop_loss, confidence, reward_risk_ratio, generated_at, generated_at_ms, expires_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                (
                    signal.symbol, signal.timeframe, signal.mode, signal.strategy, signal.direction,
                    signal.entry, signal.take_profit, signal.stop_loss, signal.confidence, signal.reward_risk_ratio,
                    signal.generated_at, _iso_to_ms(signal.generated_at), signal.expires_at,
                ),
            )
            self._conn.commit()
            return cursor.lastrowid

    def resolve_pending(self, symbol: str, timeframe: str, df: pd.DataFrame) -> int:
        """Check pending rows for (symbol, timeframe) against `df`'s
        candles, resolving any that have hit TP/SL/expiry. Returns the
        number resolved."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, direction, take_profit, stop_loss, generated_at_ms, expires_at
                FROM signal_log WHERE symbol = ? AND timeframe = ? AND status = 'pending'
                """,
                (symbol, timeframe),
            ).fetchall()
            resolved_count = 0
            for row_id, direction, take_profit, stop_loss, generated_at_ms, expires_at in rows:
                future = df[df["open_time"] > generated_at_ms]
                if future.empty:
                    continue
                expires_at_ms = _iso_to_ms(expires_at) if expires_at else None
                outcome, resolved_ms = resolve_signal(direction, take_profit, stop_loss, expires_at_ms, future)
                if outcome == "pending":
                    continue
                self._conn.execute(
                    "UPDATE signal_log SET status = ?, resolved_at = ? WHERE id = ?",
                    (outcome, _ms_to_iso(resolved_ms), row_id),
                )
                resolved_count += 1
            if resolved_count:
                self._conn.commit()
            return resolved_count

    def win_rate(self, strategy_id: str, confidence: int | None = None, band: int = 15, lookback_days: float | None = None) -> tuple[float, int] | None:
        """Empirical hit-rate among resolved trades for `strategy_id`,
        optionally restricted to a confidence band and/or a trailing
        lookback window."""
        with self._lock:
            query = "SELECT confidence, status FROM signal_log WHERE strategy = ? AND status IN ('hit_tp', 'hit_sl')"
            params: list = [strategy_id]
            if lookback_days is not None:
                cutoff_ms = int((datetime.now(timezone.utc).timestamp() - lookback_days * 86400) * 1000)
                query += " AND generated_at_ms >= ?"
                params.append(cutoff_ms)
            rows = self._conn.execute(query, params).fetchall()
        if confidence is not None:
            rows = [(c, s) for c, s in rows if abs(c - confidence) <= band]
        if not rows:
            return None
        wins = sum(1 for _, status in rows if status == "hit_tp")
        return wins / len(rows), len(rows)

    def list_recent(self, limit: int = 100) -> list[dict]:
        with self._lock:
            cursor = self._conn.execute(
                f"SELECT {', '.join(_LIST_COLUMNS)} FROM signal_log ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            rows = cursor.fetchall()
        return [dict(zip(_LIST_COLUMNS, row)) for row in rows]

    def export_csv(self, path: str | Path) -> None:
        rows = self.list_recent(limit=1_000_000)
        with Path(path).open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(_LIST_COLUMNS))
            writer.writeheader()
            writer.writerows(rows)
