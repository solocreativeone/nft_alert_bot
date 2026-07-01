# 🤖 NFT Alert Bot

A lightweight Ethereum NFT bot and **on-chain event crawler** that tracks
**floor price movements**, **new mints**, **new collection drops**, and **upcoming launches** -
and sends real-time alerts straight to your Telegram.

No webhooks. No complex infrastructure. One service. Runs forever on Railway's free tier.

---

## Features

- 📅 **Pre-Mint Calendar** - monitors OpenSea for newly launched collections and alerts you before they sell out
- 🔥 **Live & Upcoming Mints** - tracks OpenSea's curated live and upcoming mint list in real-time
- 🆕 **New Drop Alerts** - blockchain event listener that detects brand new ERC-721/1155 collections the moment they start minting on Ethereum
- 🚨 **Floor Drop Alerts** - get notified when a collection's floor falls below your target
- 🚀 **Floor Pump Alerts** - get notified when a collection's floor rises above your target
- 🟢 **Mint Tracker** - detects new mints in real-time by polling on-chain transfer events
- 📬 **Telegram delivery** - all alerts go straight to your Telegram DM
- 🤖 **Telegram commands** - manage your watchlist directly from Telegram
- ☁️ **Railway-ready** - deploys as a background worker, runs 24/7 for free

---

## Prerequisites

- Python 3.10+
- A [Telegram bot token](https://t.me/BotFather)
- An [OpenSea API key](https://opensea.io/developers) (free tier)
- An [Alchemy API key](https://alchemy.com) (free tier) - for new drop detection

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/solocreativeone/NFT_Alert_Bot.git
cd NFT_Alert_Bot
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Create your Telegram bot

1. Open Telegram → search **@BotFather**
2. Send `/newbot` and follow the prompts
3. Copy the **API token** it gives you
4. Start a chat with your new bot
5. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` to find your **chat ID**

### 4. Get an OpenSea API key

Sign up at [opensea.io/developers](https://opensea.io/developers) and copy your API key from the dashboard. No credit card required on the free tier.

### 5. Configure your collections

Copy `config.example.py` to `private/config_live.py` and fill in your real values for local development:

```python
COLLECTIONS = [
    {
        "name": "Bored Ape Yacht Club",
        "slug": "boredapeyachtclub",
        "contract": "0xBC4CA0EdA7647A8aB7C2061c2E118A18a936f13D",
        "floor_alert_low": 10.0,
        "floor_alert_high": 20.0,
    },
]
```

The `private/` folder is blocked by `.gitignore` and never pushed to GitHub.

### 6. Set environment variables

Create a `.env` file locally (never commit this):

```
TELEGRAM_TOKEN=your_token_here
CHAT_ID=your_chat_id_here
OPENSEA_API_KEY=your_opensea_key
ALCHEMY_API_KEY=your_alchemy_key
```

Or export them directly:

```bash
export TELEGRAM_TOKEN=...
export CHAT_ID=...
export OPENSEA_API_KEY=...
export ALCHEMY_API_KEY=...
```

### 7. Run locally

```bash
python bot.py
```

---

## Telegram Commands

Manage your watchlist directly from Telegram without editing any files:

| Command | Description |
|---|---|
| `/start` | Welcome message and command overview |
| `/watch 0xContract` | Add a collection to your watchlist |
| `/unwatch 0xContract` | Remove a collection from your watchlist |
| `/list` | Show all currently watched collections |
| `/live` | Check live and upcoming mints on OpenSea right now |
| `/help` | Show all available commands |

**Example:**
```
/watch 0xbd3531da5cf5857e7cfaa92426877b022e612cf8
```
Bot replies:
```
✅ Now watching: Pudgy Penguins
Current floor: 4.456 ETH
🚨 Alert low: 4.450 ETH
🚀 Alert high: 4.50 ETH
```

---

## Deploy to Railway

1. Push this repo to GitHub
2. Go to [railway.app](https://railway.app) → **New Project → Deploy from GitHub repo**
3. Connect your GitHub account and select your repo
4. Go to the **Variables** tab and add the following:
   - `TELEGRAM_TOKEN`
   - `CHAT_ID`
   - `OPENSEA_API_KEY`
   - `ALCHEMY_API_KEY`

**Optional** - override default poll intervals via Railway Variables:

| Variable | Default | Description |
|---|---|---|
| `FLOOR_CHECK_INTERVAL` | `5` | Floor price check frequency (minutes) |
| `MINT_CHECK_INTERVAL` | `1` | Mint tracker frequency (minutes) |
| `DROPS_CHECK_INTERVAL` | `1` | New drop detection frequency (minutes) |
| `MINT_COOLDOWN_MINUTES` | `10` | Min gap between mint alerts per collection |
| `MIN_MINTS_THRESHOLD` | `5` | Min mints before alerting on a new drop |
| `FLOOR_COOLDOWN_MINUTES` | `30` | Min gap between floor alerts per collection |

5. Railway auto-detects `railway.toml` and deploys automatically

Railway will keep the bot running 24/7. Monitor logs under the **Deployments** tab.

---

## Project Structure

```
nft-alert-bot/
├── bot.py                # Entry point - schedules and runs everything
├── floor.py              # Floor price checker and alert logic
├── mint.py               # Mint tracker via polling
├── drops.py              # New drop detector via Alchemy
├── live_drops.py         # Live & upcoming mints via OpenSea
├── calendar_tracker.py   # Pre-mint calendar via OpenSea API
├── commands.py           # Telegram bot commands (/watch /unwatch /list /live)
├── watchlist.py          # Persistent watchlist - saved to watchlist.json
├── config.py             # Collections config and env var loading
├── config.example.py     # Sample config for contributors
├── railway.toml          # Railway deployment config
├── requirements.txt
└── .gitignore
```

---

## Roadmap

- [x] Pre-mint calendar alerts
- [x] Live & upcoming mints tracker
- [x] Telegram commands for watchlist management
- [ ] Multi-chain support (Solana, Base)
- [ ] Discord alerts
- [ ] Web dashboard
- [ ] Gas price alerts
- [ ] Wallet activity tracker

---

## License

MIT - free to use, modify, and distribute.

---

## Contributing

PRs welcome. Open an issue first for major changes.
