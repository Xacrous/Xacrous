"""Daily candle sources: a crypto exchange (via ccxt) or a local CSV/XLSX file."""
from __future__ import annotations

import csv
import time
from pathlib import Path

from .backtest import Candle

DAY_MS = 86_400_000

# Column aliases for files exported from CoinMarketCap, TradingView, Binance, etc.
_ALIASES = {
    "ts": ["timeopen", "time", "timestamp", "date", "open time", "opentime"],
    "open": ["open", "priceopen"],
    "high": ["high", "pricehigh"],
    "low": ["low", "pricelow"],
    "close": ["close", "priceclose"],
    "volume": ["volume", "vol"],
}


def load_file(path: str | Path) -> list[Candle]:
    """Load daily OHLC candles from a .csv or .xlsx file, oldest first."""
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        import warnings
        import openpyxl
        warnings.filterwarnings("ignore", message="Workbook contains no default style")
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        rows = list(wb.worksheets[0].iter_rows(values_only=True))
    else:
        with path.open(newline="") as f:
            sample = f.read(4096)
            f.seek(0)
            rows = list(csv.reader(f, delimiter=";" if sample.count(";") > sample.count(",") else ","))
    header = [str(h).strip().lower() for h in rows[0]]
    idx = {}
    for key, names in _ALIASES.items():
        for name in names:
            if name in header:
                idx[key] = header.index(name)
                break
    missing = {"ts", "open", "high", "low", "close"} - idx.keys()
    if missing:
        raise ValueError(f"{path.name}: missing columns {sorted(missing)} (found {header})")
    candles = {}
    for row in rows[1:]:
        if not row or row[idx["close"]] in (None, ""):
            continue
        ts = _to_ms(row[idx["ts"]])
        candles[ts] = Candle(ts, float(row[idx["open"]]), float(row[idx["high"]]),
                             float(row[idx["low"]]), float(row[idx["close"]]),
                             float(row[idx["volume"]] or 0) if "volume" in idx else 0.0)
    return [candles[k] for k in sorted(candles)]


def _to_ms(value) -> int:
    from datetime import datetime, timezone
    if isinstance(value, datetime):
        return int(value.replace(tzinfo=value.tzinfo or timezone.utc).timestamp() * 1000)
    s = str(value).strip()
    try:
        num = float(s)
        return int(num if num > 1e11 else num * 1000)  # ms or seconds
    except ValueError:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00").replace("/", "-"))
        return int(dt.replace(tzinfo=dt.tzinfo or timezone.utc).timestamp() * 1000)


def closed_only(candles: list[Candle], now_ms: int | None = None) -> list[Candle]:
    """Drop the still-forming candle: a daily candle is closed once ts + 1 day has passed."""
    now_ms = now_ms or int(time.time() * 1000)
    return [c for c in candles if c.ts + DAY_MS <= now_ms]


def fetch_exchange(exchange, symbol: str, since_ms: int | None = None, limit: int = 1000) -> list[Candle]:
    """Fetch daily candles from a ccxt exchange, paging forward from ``since_ms``."""
    out: dict[int, Candle] = {}
    since = since_ms
    while True:
        batch = exchange.fetch_ohlcv(symbol, "1d", since=since, limit=limit)
        for ts, o, h, l, c, v in batch:
            out[int(ts)] = Candle(int(ts), float(o), float(h), float(l), float(c), float(v or 0))
        if since is None or len(batch) < limit:
            break
        since = int(batch[-1][0]) + DAY_MS
    return [out[k] for k in sorted(out)]
