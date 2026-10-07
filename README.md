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

## Deploy on DigitalOcean

Step-by-step guide: **[DEPLOY.md](DEPLOY.md)**. In short:

1. Create an Ubuntu 24.04 droplet **outside the US** (binance.com blocks US
   IPs), with an SSH key, and point a domain name at it.
2. Run `deploy/setup-droplet.sh` on it. This disables root and password SSH,
   turns on a firewall, fail2ban and automatic security updates, and installs
   Docker.
3. Create a Binance API key restricted to the droplet's IP, with Spot
   trading on and withdrawals off.
4. Fill in `.env`, then `docker compose up -d --build`. Caddy gets an HTTPS
   certificate automatically.

## Security

| Layer | What protects it |
|---|---|
| **Network** | Only ports 22/80/443 are open (ufw, plus an optional DigitalOcean Cloud Firewall). The bot's port is never published; only Caddy can reach it. You can optionally set `ALLOWED_IPS` so only your own IPs can open the site. |
| **Transport** | HTTPS only, with Let's Encrypt certificates, HSTS and http→https redirects. |
| **Login** | Password stored as a scrypt hash. Two-factor (TOTP) codes are required in live mode, and each code works only once. |
| **Brute-force protection** | Lockout after 5 failed logins per IP, or 20 in total, within 15 minutes. Failed logins are recorded in the activity log. |
| **Sessions** | Random server-side tokens in an HttpOnly, Secure, SameSite=Strict cookie. They expire after 60 minutes idle or 12 hours, and a restart logs everyone out. |
| **Browser** | Every state-changing request must carry a custom header and a same-origin `Origin` (CSRF protection). Strict Content-Security-Policy (no inline scripts, no third-party code), `X-Frame-Options: DENY`, no-referrer. The API docs pages are switched off. |
| **Binance key** | The bot checks the key at startup and every 6 hours. It **refuses to trade if the key can withdraw** or lacks spot-trading permission, and warns if the key is not IP-restricted. Even a stolen key can then only trade, never move funds out. |
| **Container** | Runs as a non-root user on a read-only filesystem, with all Linux capabilities dropped, `no-new-privileges` and a memory limit. Secrets come from `.env` (chmod 600) and are never baked into the image. |
| **Server** | SSH keys only, no root login, fail2ban, unattended security upgrades. |

## Run it locally

```bash
pip install -r requirements.txt
python -m app.cli set-password        # paste the printed line into .env
COOKIE_SECURE=false DATA_FILE=data/btc_daily.xlsx \
  uvicorn app.main:app_factory --factory --port 8000
# open http://localhost:8000
```

Run the tests with `pip install -r requirements-dev.txt && pytest`.

## Going live (real money)

1. Run in **paper mode** with live prices (leave `DATA_FILE` empty) for a
   while and watch the dashboard.
2. Test your Binance connection with **fake money** first: create keys at
   [testnet.binance.vision](https://testnet.binance.vision), then set
   `MODE=live` and `TESTNET=true`. Signals still use real prices; orders go
   to the testnet.
3. Create your real Binance API key (see [DEPLOY.md](DEPLOY.md#3-binance-api-key))
   and run `docker compose exec bot python -m app.cli check-binance`.
4. Set `TESTNET=false`, restart, log in and press **Resume trading**. On first
   launch in live mode the bot starts paused.

Things to know:

* **Startup buy:** if price is already above the buy band when you resume,
  the bot buys at once, because the strategy is "in" during that phase of
  the trend. Use `ALLOCATION` to limit how much USDT a buy uses.
* **Your own BTC is safe:** the bot only sells BTC that it bought itself
  (tracked in its database). BTC you already held is never sold.
* **Repeat checks are harmless:** the bot checks every 15 minutes, but acts
  only on closed daily candles. It buys only when flat and sells only when
  holding, so restarts or repeated checks never double-trade.
* **Stale data:** the bot refuses to trade if the newest closed candle is
  more than 2 days old.
* **Keep the database:** it records what the bot holds. If it is lost while
  the bot is holding BTC, the bot will think it is flat and won't sell. Turn
  on droplet backups (see DEPLOY.md).

## Configuration

All settings are environment variables. See [`.env.example`](.env.example).

| Variable | Default | Meaning |
|---|---|---|
| `MODE` / `TESTNET` | `paper` / `false` | `paper` or `live`. `TESTNET=true` sends live-mode orders to the Binance Spot Testnet |
| `API_KEY` / `API_SECRET` | – | Binance API key (live mode) |
| `EXCHANGE` / `SYMBOL` | `binance` / `BTC/USDT` | ccxt exchange id and market |
| `ALLOCATION` | `1.0` | Share of free quote balance used per buy |
| `SMA_LENGTH`, `ENTRY_BAND`, `EXIT_BAND` | `100`, `0.06`, `0.04` | Strategy parameters |
| `PAPER_START_QUOTE`, `FEE` | `10000`, `0.001` | Paper account size and fee |
| `DATA_FILE` | – | CSV/XLSX daily candles instead of the exchange (paper only) |
| `DASHBOARD_USER` / `DASHBOARD_PASSWORD_HASH` | `admin` / – | Login (hash from `python -m app.cli set-password`) |
| `TOTP_SECRET` | – | Two-factor secret from `python -m app.cli setup-2fa` (required in live mode) |
| `COOKIE_SECURE` | `true` | Set `false` only for plain-http local testing |
| `SESSION_IDLE_MIN` / `SESSION_MAX_HOURS` | `60` / `12` | Session lifetime |
| `DOMAIN`, `ACME_EMAIL`, `ALLOWED_IPS` | – | Caddy / HTTPS settings for docker compose |
| `CHECK_INTERVAL_MIN` | `15` | How often the bot checks for a new closed candle |
| `DB_PATH` | `data/bot.db` | SQLite file for state, orders and the activity log |

## API

All endpoints except `/api/login`, `/api/login-info` and `/healthz` need a
logged-in session. POSTs must send `X-Requested-With: btcbot`.

| Method | Path | |
|---|---|---|
| POST | `/api/login`, `/api/logout` | Session login (`username`, `password`, `code`) / logout |
| GET | `/api/status` | Mode, price, position, balances, Binance account check, last signal |
| GET | `/api/candles?limit=` | Closed daily candles with SMA and bands |
| GET | `/api/backtest?sma_length=&entry_band=&exit_band=` | Backtest on the loaded history |
| GET | `/api/orders`, `/api/events` | Bot orders and activity log |
| POST | `/api/run` | Check the latest close now |
| POST | `/api/verify` | Re-check the Binance API key now |
| POST | `/api/pause`, `/api/resume` | Kill switch |

## Code layout

```
app/strategy.py   signal rules (pure functions)
app/backtest.py   backtester with daily mark-to-market equity
app/data.py       candles from the exchange (ccxt) or a CSV/XLSX file
app/broker.py     PaperBroker and ExchangeBroker (Binance via ccxt, key checks)
app/engine.py     the daily trading loop
app/security.py   password hashing, TOTP, sessions, login lockout
app/cli.py        set-password, setup-2fa, check-binance
app/main.py       FastAPI app, login, security headers, background loop
app/static/       dashboard (TradingView Lightweight Charts, Apache-2.0)
deploy/           Caddyfile and droplet hardening script
tests/            pytest suite
```
