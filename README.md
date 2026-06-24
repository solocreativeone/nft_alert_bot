# 🤖 NFT Alert Bot

A lightweight Ethereum NFT bot and **on-chain event crawler** that tracks 
**floor price movements**, **new mints**, and **new collection drops** -
and sends real-time alerts straight to your Telegram.

No webhooks. No complex infrastructure. One service. Runs forever on Render's free tier.

---

## Features

- 🆕 **New Drop Alerts** - blockchain event listener that detects brand new 
  ERC-721/1155 collections the moment they start minting on Ethereum
- 🚨 **Floor Drop Alerts** - get notified when a collection's floor falls below your target
- 🚀 **Floor Pump Alerts** - get notified when a collection's floor rises above your target
- 🟢 **Mint Tracker** - detects new mints in real-time by polling on-chain transfer events
- 📬 **Telegram delivery** - all alerts go straight to your Telegram DM
- ☁️ **Render-ready** - deploys as a background worker, runs 24/7 for free
 
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
git clone https://github.com/solocreativeone/nft-alert-bot.git
cd nft-alert-bot
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

Edit `config.py`:

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
export ALCHEMY_API_KEY=your_alchemy_key=...
```

### 7. Run locally

```bash
python bot.py
```

---

## Deploy to Render

1. Push this repo to GitHub
2. Go to [render.com](https://render.com) → **New → Background Worker**
3. Connect your GitHub repo
4. Set the following environment variables in the Render dashboard:
   - `TELEGRAM_TOKEN`
   - `CHAT_ID`
   - `OPENSEA_API_KEY`
   - `ALCHEMY_API_KEY`

5. Click **Deploy**

Render will keep the bot running 24/7 on the free tier.

---

## Project Structure

```
nft-alert-bot/
├── drops.py         # New drop detector via Alchemy
├──bot.py           # Entry point - schedules and runs everything
├── floor.py         # Floor price checker and alert logic
├── mint.py          # Mint tracker via polling
├── config.py        # Collections config and env var loading
├── render.yaml      # Render deployment config
├── requirements.txt
└── .gitignore
```

---

## Roadmap

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
