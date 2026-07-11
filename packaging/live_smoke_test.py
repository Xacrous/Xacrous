"""Real-network smoke test — run only in CI (this repo's own dev sandbox
blocks api.binance.com). Exercises the full pipeline against live Binance
data: REST candles, every registered strategy, a walk-forward backtest,
and a few seconds of the real WebSocket feed. Any uncaught exception fails
the CI step; each section prints its own progress so a failure is easy to
place.
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chartpilot.data_fetcher.cache import CandleCache
from chartpilot.data_fetcher.exchange_client import ExchangeClient
from chartpilot.signal_engine.backtester import run_backtest
from chartpilot.signal_engine.registry import list_strategies
from chartpilot.ta_engine.indicators import CANDLE_LIMIT, compute


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cache = CandleCache(Path(tmp) / "cache.sqlite3")
        client = ExchangeClient(cache)

        print("== validate_symbol ==")
        unified = client.validate_symbol("BTCUSDT")
        assert unified == "BTC/USDT", f"unexpected unified symbol: {unified}"
        print(f"OK: BTCUSDT -> {unified}")

        for mode in ("swing", "scalp"):
            timeframe = "4h" if mode == "swing" else "5m"
            limit = CANDLE_LIMIT[mode]
            print(f"\n== {mode} mode: fetching {limit} {timeframe} candles ==")
            df = client.get_candles("BTCUSDT", timeframe, limit=limit)
            assert len(df) == limit, f"expected {limit} candles, got {len(df)}"
            assert df["close"].notna().all(), "NaNs in real close data"
            df.attrs["symbol"] = "BTCUSDT"
            df.attrs["timeframe"] = timeframe
            print(f"OK: fetched {len(df)} candles, last close = {df['close'].iloc[-1]}")

            print(f"-- computing {mode} indicators --")
            indicators = compute(df, mode=mode)
            print("OK: indicators computed")

            print(f"-- evaluating all {mode} strategies --")
            for strategy in list_strategies(mode=mode):
                signal = strategy.evaluate(df, indicators)
                status = f"SIGNAL ({signal.direction}, confidence={signal.confidence})" if signal else "no signal"
                print(f"  {strategy.id}: {status}")

            print(f"-- backtesting {mode}/trend_following-equivalent (first registered strategy) --")
            strategy = list_strategies(mode=mode)[0]
            result = run_backtest(df, mode, strategy)
            print(f"OK: {result.total_trades} trades, win_rate={result.win_rate}, expectancy={result.expectancy}")

        print("\n== WebSocket live feed (ticker + kline), ~15s ==")
        events: list[tuple[str, dict]] = []
        stop_event = threading.Event()

        def on_update(kind: str, payload: dict) -> None:
            events.append((kind, payload))

        thread = threading.Thread(target=client.subscribe_live, args=("BTCUSDT", "1m", on_update, stop_event))
        thread.start()
        time.sleep(15)
        stop_event.set()
        thread.join(timeout=10)
        assert not thread.is_alive(), "live feed thread failed to stop"

        kinds_seen = {kind for kind, _ in events}
        print(f"OK: received {len(events)} events, kinds={kinds_seen}")
        assert "ticker" in kinds_seen, "never received a ticker update from the live WebSocket feed"

        cache.close()

    print("\nALL LIVE SMOKE TESTS PASSED")


if __name__ == "__main__":
    main()
