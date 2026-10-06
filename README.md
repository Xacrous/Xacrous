# BTC Trend Bot

A web app that trades Bitcoin on the **daily chart** using a trend filter
built from the 100-day moving average and two bands around it. It runs as a
small Python server with a dashboard, in **paper mode** (simulated money) by
default or in **live mode** on Binance (or any exchange
[ccxt](https://github.com/ccxt/ccxt) supports).

![dashboard](docs/dashboard.png)

## The strategy

Every day after the daily candle closes:

| | Rule |
|---|---|
| **Buy** | Not holding, and the close is more than **6% above the 100-day SMA** |
| **Sell** | Holding, and the close is more than **4% below the 100-day SMA** |
| **Otherwise** | Keep the current position |

The gap between the two bands stops the bot from flipping in and out while
price chops around the average. It is long-only (no shorting, no leverage).

### Backtest (`data/btc_daily.xlsx`, Nov 2018 – Oct 2026, 0.1% fee per side, next-day-open fills)

| | Strategy | Buy & hold |
|---|---|---|
| Yearly return (CAGR) | **61.9%** | 46.6% |
| Worst drop (max drawdown) | **−38.7%** | −76.6% |
| Closed trades / win rate | 11 / 63.6% | — |
| Profit factor | 15.3 | — |
| Time in the market | 56% | 100% |

How the parameters were chosen and checked:

* **Settings:** 96 combinations were tested (SMA 80–130, entry band 2–8%,
  exit band 2–8%). All of them were profitable on 2023–2026 data, which was
  not used to choose them.
* **Nearby values:** settings next to the chosen one (SMA 100, entry 4–8%,
  exit 2–4%) all give 59–66% a year.
* **Rejected – RSI(4) dip buying:** an earlier strategy based on 4-period RSI
  dips looked great on a single year (85% win rate). Over the full 8 years it
  made only about 4% a year, with a 62% drawdown.
* **Rejected – trailing stops:** they lowered returns and did not reduce the
  drawdown.

**Limits:** it wins about 2 trades in 3. The money comes from a few big
trends (+464% in 2020–21, +129% in 2019, +112% in 2023–24), and the losers
are small (−10% to −16%). Expect long flat periods and whipsaw losses in
sideways markets. Past results do not guarantee future returns.

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env          # edit as needed
uvicorn app.main:app_factory --factory --port 8000
# open http://localhost:8000
```

To try it without any exchange connection, run paper mode on the included
history file:

```bash
DATA_FILE=data/btc_daily.xlsx uvicorn app.main:app_factory --factory --port 8000
```

With Docker:

```bash
docker build -t btc-bot .
docker run -d --name btc-bot -p 8000:8000 --env-file .env -v btc-bot-data:/data btc-bot
```

Run the tests with `pip install -r requirements-dev.txt && pytest`.

## Going live (real money)

1. Run in **paper mode** with live exchange data (leave `DATA_FILE` empty)
   for a while and watch the dashboard.
2. Create a Binance API key with **Spot trading enabled and withdrawals
   disabled**, and restrict it to your server's IP address.
3. In `.env`, set `MODE=live`, `API_KEY`, `API_SECRET` and a strong
   `DASHBOARD_PASSWORD`. Live mode will not start without all three. Use
   `ALLOCATION` to limit how much of your free USDT each buy spends.
4. Start the app. **On first launch in live mode it starts paused.** Check
   the dashboard, then press **Resume trading**.

Things to know:

* **Startup buy:** if price is already above the buy band when you resume,
  the bot buys at once, because the strategy is "in" during that phase of
  the trend.
* **Your own BTC is safe:** the bot only sells BTC that it bought itself
  (tracked in its database). BTC you already held is never sold.
* **Repeat checks are harmless:** the bot checks every 15 minutes, but acts
  only on closed daily candles. It buys only when flat and sells only when
  holding, so restarts or repeated checks never double-trade.
* **Stale data:** the bot refuses to trade if the newest closed candle is
  more than 2 days old.
* **Use HTTPS:** put the dashboard behind HTTPS (a reverse proxy like Caddy
  or nginx) if it is reachable from the internet.

## Configuration

All settings are environment variables. See [`.env.example`](.env.example).

| Variable | Default | Meaning |
|---|---|---|
| `MODE` | `paper` | `paper` or `live` |
| `EXCHANGE` / `SYMBOL` | `binance` / `BTC/USDT` | ccxt exchange id and market |
| `ALLOCATION` | `1.0` | Share of free quote balance used per buy |
| `SMA_LENGTH`, `ENTRY_BAND`, `EXIT_BAND` | `100`, `0.06`, `0.04` | Strategy parameters |
| `PAPER_START_QUOTE`, `FEE` | `10000`, `0.001` | Paper account size and fee |
| `DATA_FILE` | – | CSV/XLSX daily candles instead of the exchange (paper only) |
| `DASHBOARD_USER` / `DASHBOARD_PASSWORD` | `admin` / – | HTTP Basic auth for the dashboard and API |
| `CHECK_INTERVAL_MIN` | `15` | How often the bot checks for a new closed candle |
| `DB_PATH` | `data/bot.db` | SQLite file for state, orders and the activity log |

## API

| Method | Path | |
|---|---|---|
| GET | `/api/status` | Mode, price, position, balances, last signal and bands |
| GET | `/api/candles?limit=` | Closed daily candles with SMA and bands |
| GET | `/api/backtest?sma_length=&entry_band=&exit_band=` | Backtest on the loaded history |
| GET | `/api/orders`, `/api/events` | Bot orders and activity log |
| POST | `/api/run` | Check the latest close now |
| POST | `/api/pause`, `/api/resume` | Kill switch |

## Code layout

```
app/strategy.py   signal rules (pure functions)
app/backtest.py   backtester with daily mark-to-market equity
app/data.py       candles from the exchange (ccxt) or a CSV/XLSX file
app/broker.py     PaperBroker and ExchangeBroker (market orders)
app/engine.py     the daily trading loop
app/main.py       FastAPI app, dashboard and background loop
app/static/       dashboard (TradingView Lightweight Charts, Apache-2.0)
tests/            pytest suite
```
