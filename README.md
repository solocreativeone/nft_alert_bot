# 🤖 NFT Alert Bot

### *Your Real-Time Gateway to the NFT Space - Direct to Telegram* 🚀

An intelligent, multi-chain NFT crawler and tracker that monitors new collection drops on **Ethereum, Polygon, Base, Arbitrum, Optimism, and Robinhood Chain**, alongside upcoming mint schedules on **NFTCalendar**. It delivers real-time alerts straight to your Telegram DM, complete with high-resolution image previews, on-chain legitimacy scoring by **Gemini AI**, DEX liquidity detection, deployer history tracking, and direct OpenSea/explorer links.

No webhooks, no complex database setups, and zero maintenance.

> ## 🚧 Project Status: Local Development
>
> **This bot is currently run locally only. It is not deployed to any cloud host.**
> Development and testing happen on local machines (> chosen yet.
>
> A `railway.toml` is present and Railway instructions are included below, but they
> are **untested/aspirational**. Treat them as a future option, not the current
> setup. Because state is in-memory only (see Caveats), each local run starts with
> an empty dedup store.

---

## 🌟 Key Features (In Plain English)

- 🧠 **Gemini AI Legitimacy & Risk Auditor:** Powered by Google's `gemini-3.5-flash-lite`. Every newly detected drop is inspected for:
  - **On-chain Wallet Distribution:** Unique minters vs. total mint count (identifying bot minting and insider wash-trading).
  - **Mint Velocity Analysis:** Real-time velocity tracking (mints/hour) to spot artificial spikes and bot sweeps.
  - **Decentralized Metadata Verification:** Inspects `tokenURI` for IPFS, Arweave, or on-chain SVG storage versus broken/centralized URLs.
  - **Contract & Identity Audit:** Evaluates verified source code from Blockscout, detects copycats, trademark infringement, and unrenounced backdoors.
  - **Deployer Reputation & History:** Incorporates creator track record and past rug flags into the assessment.
  - **DEX Pool Backing:** Factors active token/NFT liquidity depth into the legitimacy score.
  - **Natural Language Briefs:** Provides concise 1-2 sentence executive summaries directly in Telegram alerts.
- 🛑 **Deployer History Cache & Serial-Rug Fast-Block:** Tracks creator wallet history in a lightweight local cache (`deployers.json`). Known serial ruggers are blocked instantly before calling Gemini, saving API credits and reducing alert latency.
- 💧 **Real-Time DEX Liquidity Detection:** Integrates DexScreener to check whether a contract has active trading liquidity (e.g. Uniswap or PancakeSwap pairs), reporting pool depth and 24h trading volume directly in alerts.
- ⚡ **Optimized Robinhood Chain Tracker:** High-throughput batch RPC querying collapses multiple contract calls into a single log scan, eliminating rate limits (429s) while extracting unique minter metrics.
- 🔗 **Direct OpenSea & Explorer Links:** Every alert includes direct links to block explorers (Blockscout, Etherscan) and direct asset pages on OpenSea, letting you inspect and trade immediately without copy-pasting contract addresses.
- 📡 **Multi-Pipeline & Multi-Chain Monitoring:**
  - **On-Chain Drops:** Real-time mint detection across Ethereum, Polygon, Base, Arbitrum, Optimism, and Robinhood Chain.
  - **NFTCalendar Drops:** Upcoming and live mint schedules from the Web's largest drop aggregator.
- 🛡️ **Cloudflare Bypass Tech:** Uses `curl_cffi` for browser TLS/JA3 impersonation to scrape drop aggregators without hitting `403 Forbidden` barriers.
- 🖼️ **Reliable Image Delivery:** Downloads image bytes in-memory and uploads them directly to Telegram's photo API, avoiding broken image previews.
- 🧠 **Smart Deduplication:** Memory-bounded caching prevents duplicate alerts for already-seen contracts and scheduled drops.

---

## 📊 Sample Telegram Alert Preview

```text
🆕 New NFT Drop Detected!

Pool's Closed Guards Gen 2 (GUARD2)
🔗 Chain: Robinhood
📄 Contract: 0x2117...5445
👤 Creator: 0x89ab...c120
🏷️ Standard: ERC-721
🔥 Mints: 78 (19.1h old) | 👥 Minters: 20
💧 Liquidity: $24.5K (Uniswap) | 24h Vol: $12.3K

AI Legitimacy Audit:
✅ Looks Legit (85/100)
💡 The collection shows healthy wallet diversity with 20 unique minters across 78 mints, a steady mint velocity, and fully on-chain metadata via Base64 encoded SVGs, indicating a legitimate and well-constructed project.

[ 🔍 Blockscout ]  [ 🌊 OpenSea ]  [ 📈 Uniswap Chart ]
```

---

## 🛠️ Prerequisites

Before you start, make sure you have:
1. **Python 3.10+** installed on your system.
2. A **Telegram Bot Token** (Create one via [@BotFather](https://t.me/BotFather) on Telegram).
3. Your **Telegram Chat ID** (Find it by sending a message to your bot and checking `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`).
4. A **Gemini API Key** (Free tier available at [aistudio.google.com](https://aistudio.google.com)).
5. An **OpenSea API Key** (Available at [opensea.io/developers](https://opensea.io/developers)).
6. An **Alchemy API Key** (Free tier available at [alchemy.com](https://alchemy.com) for EVM chains).

---

## 🚀 Quick Start & How to Run

### 1. Clone & Navigate to the Repository

```bash
git clone https://github.com/solocreativeone/NFT_Alert_Bot.git
cd NFT_Alert_Bot
```

### 2. Set Up a Virtual Environment & Activate

```bash
# Create venv
python3 -m venv .venv

# Activate venv (macOS/Linux)
source .venv/bin/activate

# Activate venv (Windows)
.venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configuration

You can configure the bot in two ways:

#### Option A: Using Environment Variables (`.env`)
Create a `.env` file in the project root:

```env
TELEGRAM_TOKEN=your_telegram_bot_token_here
CHAT_ID=your_telegram_chat_id_here
OPENSEA_API_KEY=your_opensea_api_key_here
ALCHEMY_API_KEY=your_alchemy_api_key_here
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MIN_SCORE=40
```

#### Option B: Using a Configuration File (`config.py`)
Modify `config.py` in the root directory, or copy to `private/config_live.py` (which is git-ignored):

```python
TELEGRAM_TOKEN = "your_telegram_bot_token_here"
CHAT_ID = "your_telegram_chat_id_here"
OPENSEA_API_KEY = "your_opensea_api_key_here"
ALCHEMY_API_KEY = "your_alchemy_api_key_here"
GEMINI_API_KEY = "your_gemini_api_key_here"
GEMINI_MIN_SCORE = 40
```

### 5. Launch the Bot

```bash
python bot.py
```

---

## 🎯 Scoring & Filter Thresholds

| Score Range | Verdict Badge | Outcome |
|---|---|---|
| **60 - 100** | ✅ **Looks Legit** | High-confidence launch; alerted with executive summary. |
| **40 - 59** | ⚠️ **Suspicious / High Risk** | Speculative or unverified project; alerted with risk explanation. |
| **0 - 39** (or `LIKELY_RUG`) | 🚨 **Likely Rug / Bot Churn** | Automatically blocked from Telegram. |

> **Tip:** Adjust `GEMINI_MIN_SCORE` in your `.env` or Railway settings to control your notification volume. Set to `60` or `70` for strictly high-conviction drops, or keep at `40` to see early speculative projects.

---

## 💬 Telegram Commands

Manage your watchlist directly from Telegram without editing config files:

| Command | Description |
|---|---|
| `/start` | Welcome message and list of commands |
| `/watch <0xContract>` | Add a contract to your watchlist |
| `/unwatch <0xContract>` | Remove a contract from your watchlist |
| `/list` | Show your active watched collections |
| `/live` | View the top 10 upcoming Ethereum mints immediately |
| `/help` | Display command help |

---

## ☁️ Deploying to Railway (Optional / Not Currently Used)

> ⚠️ **Not the current setup.** The bot runs locally today (see Project Status at
> the top). The steps below are an untested future option, kept for whenever a
> host is chosen.

1. Push this project to your GitHub repository.
2. Log into [railway.app](https://railway.app) and create a **New Project**.
3. Select **Deploy from GitHub repo** and connect your project repository.
4. Go to **Settings/Variables** in your Railway dashboard and add:
   - `TELEGRAM_TOKEN`
   - `CHAT_ID`
   - `OPENSEA_API_KEY`
   - `ALCHEMY_API_KEY`
   - `GEMINI_API_KEY`
   - `GEMINI_MIN_SCORE` (e.g. `40`)
5. Railway will deploy from `railway.toml` and keep the bot running 24/7.

---

## 📂 Project Structure

- `bot.py`: Main entry point: runs async task loops and Telegram command listener.
- `gemini_filter.py`: Gemini AI scoring engine with on-chain context, IPFS inspection, deployer stats, and DEX liquidity.
- `deployer_cache.py`: Deployer wallet cache and serial-rug pre-filter against Blockscout creator history.
- `dex_liquidity.py`: Real-time DEX pool and volume detector powered by DexScreener.
- `drops.py`: Multi-chain mint detector, metadata reader, and batched Robinhood RPC handler.
- `notifier.py`: Shared async Telegram notification dispatcher for text and photos.
- `live_drops.py`: Scrapes upcoming mint events across chains from NFTCalendar.
- `calendar_tracker.py`: Background scheduler for long-range drop tracking.
- `commands.py`: Telegram slash command handler (/watch, /live, /unwatch, /list).
- `floor.py` / `mint.py`: Watchlist floor-price and mint-velocity trackers.
- `watchlist.py`: Local persistence layer for watched collections.
- `dedup.py`: Shared memory and daily de-duplication store.

---

## 📄 License

This project is licensed under the MIT License. Feel free to fork, modify, and build on top of it!

