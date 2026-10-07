# Deploying on DigitalOcean

This guide takes you from nothing to the bot trading on your Binance account,
served over HTTPS from a hardened droplet. Plan on about an hour.

**You need:** a DigitalOcean account, a domain name (any registrar), your
Binance account, and an authenticator app on your phone (Google
Authenticator, Authy, 1Password…).

---

## 1. Create the droplet

In DigitalOcean, go to **Create → Droplets**:

* **Region:** **not** New York, San Francisco, Atlanta (USA) or Toronto
  (Canada). Binance.com blocks those countries, and every API call from
  there fails with error 451. Frankfurt (FRA1) or Amsterdam (AMS3) work
  well. Make sure Binance is also allowed in the country you live in.
* **Image:** Ubuntu 24.04 LTS
* **Size:** Basic, Regular, **1 GB RAM** ($6/month) is enough.
* **Authentication:** **SSH key**, not a password. If you don't have one
  yet, run `ssh-keygen -t ed25519` on your computer and paste the
  `~/.ssh/id_ed25519.pub` file.
* **Backups:** turn on (weekly). The bot's database records what it holds.
* **IPv6:** leave **off**. Binance must see the single IPv4 address you
  allow-list on your API key.

Note the droplet's **IPv4 address**. This guide calls it `DROPLET_IP`.

## 2. Domain and cloud firewall

1. At your domain registrar (or DigitalOcean **Networking → Domains**), add
   an **A record**, e.g. `bot.yourdomain.com → DROPLET_IP`.
2. In DigitalOcean, open **Networking → Firewalls → Create Firewall** and
   add these inbound rules:
   * SSH (22) from **your own IP only** (pick *My IP*). If your home IP
     changes, update this rule.
   * HTTP (80) and HTTPS (443) from all IPv4 and IPv6.

   Leave the outbound rules on their defaults, then apply the firewall to
   your droplet. This firewall works on top of the one on the server.

## 3. Binance API key

In Binance, open **Profile → API Management → Create API → System
generated**, give it a label like `btc-bot`, and confirm with 2FA. Then
**Edit restrictions**:

| Setting | Value |
|---|---|
| Restrict access to trusted IPs only | **ON**, enter `DROPLET_IP` |
| Enable Reading | ON |
| Enable Spot & Margin Trading | **ON** |
| Enable Withdrawals | **OFF**. The bot refuses to trade if this is on. |
| Everything else (Futures, Margin loan, Universal transfer, …) | OFF |

Copy the **API Key** and **Secret Key** somewhere safe. Binance shows the
secret only once. Keep about 10 USDT or more in your Spot wallet: Binance
rejects orders under its minimum order size.

**Test first with fake money (recommended):** log in to
[testnet.binance.vision](https://testnet.binance.vision) with GitHub and
create an HMAC key there. Testnet keys are separate from your real account
and come with test funds.

## 4. Harden the server and install Docker

From your computer:

```bash
ssh root@DROPLET_IP
git clone -b claude/new-session-dptocr https://github.com/Xacrous/Xacrous.git /opt/btc-bot
bash /opt/btc-bot/deploy/setup-droplet.sh deploy
```

The script:
* installs updates and turns on automatic security updates;
* creates the user `deploy` with your SSH key, then turns off root login
  and password login;
* enables the firewall (22/80/443 only) and fail2ban;
* installs Docker and adds swap.

It asks you to set a sudo password for `deploy`.

**Before closing the root session,** open a second terminal and check that
`ssh deploy@DROPLET_IP` works.

Then hand the app folder to `deploy`:

```bash
sudo chown -R deploy:deploy /opt/btc-bot
```

> If the repository is private, `git clone` needs credentials. Add a
> read-only **deploy key** (GitHub → repo → Settings → Deploy keys), or
> clone with a fine-grained personal access token that has read-only access
> to this one repository.

## 5. Configure

Log in as `deploy`, then:

```bash
cd /opt/btc-bot
cp .env.example .env
chmod 600 .env
nano .env        # set DOMAIN and ACME_EMAIL first
docker compose build
```

Create your login. Each command prints a line to paste into `.env`:

```bash
docker compose run --rm --no-deps bot python -m app.cli set-password
docker compose run --rm --no-deps bot python -m app.cli setup-2fa
```

`setup-2fa` shows a key to type into your authenticator app (or a link to
open on your phone), then asks for a code to confirm it works.

Then set the trading section of `.env`. To start with the testnet:

```ini
MODE=live
TESTNET=true
API_KEY=<testnet api key>
API_SECRET=<testnet secret>
ALLOCATION=1.0
```

Optional: if you always use the dashboard from the same place, set
`ALLOWED_IPS=<your.home.ip>/32`. Everyone else is then refused before they
reach the login page.

## 6. Start it

```bash
docker compose up -d
docker compose logs -f           # Ctrl+C to stop following
docker compose exec bot python -m app.cli check-binance
```

Open `https://bot.yourdomain.com`. Caddy gets the HTTPS certificate on the
first visit, which takes a few seconds. Log in with your password and
authenticator code.

* The banner at the top should say **Connected to Binance Spot Testnet ·
  spot trading on · withdrawals off**.
* The bot starts **paused**. Press **Resume trading** when you are ready.

## 7. Switch to your real account

When you're happy with the testnet:

```bash
nano .env                 # TESTNET=false, API_KEY / API_SECRET = your real key from step 3
docker compose up -d      # recreates the bot with the new settings
docker compose exec bot python -m app.cli check-binance
```

`check-binance` must show `Withdrawals enabled: NO` and `Restricted to
trusted IPs: YES`.

Because the account changed, the bot clears its testnet position and
**pauses**. Log in, check the banner and balances, set `ALLOCATION` if you
don't want it to use all your USDT, then press **Resume trading**.

Remember: if the price is already above the buy band when you resume, the
bot buys straight away.

---

## Everyday operations

| Task | Command / action |
|---|---|
| **Stop trading now** | Dashboard → **Pause trading** (it keeps any BTC it holds), or `docker compose stop bot` |
| **Key leaked or lost?** | Delete the key in Binance API Management immediately. That instantly cuts the bot off; then create a new key. |
| Update the app | `cd /opt/btc-bot && git pull && docker compose up -d --build` |
| View logs | `docker compose logs --tail 200 bot` |
| Back up the database | `docker compose cp bot:/data/bot.db ./bot-$(date +%F).db` (plus droplet backups) |
| Change password / 2FA | Re-run `set-password` / `setup-2fa`, edit `.env`, then `docker compose up -d` |
| Log everyone out | `docker compose restart bot` |

## Security checklist

- [ ] Droplet uses SSH-key login only; `ssh root@DROPLET_IP` is refused
- [ ] DigitalOcean Cloud Firewall: SSH from your IP only, 80/443 open
- [ ] `.env` is `chmod 600` and never committed or shared
- [ ] Binance key: IP-restricted to the droplet, Spot trading on, **withdrawals off**
- [ ] Your Binance account itself has 2FA and an anti-phishing code
- [ ] Dashboard: long password plus authenticator code
- [ ] Optional: `ALLOWED_IPS` set to your own IP(s)
- [ ] Droplet backups enabled
