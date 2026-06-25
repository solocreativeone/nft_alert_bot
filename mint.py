import requests
import asyncio
from datetime import datetime, timezone, timedelta
from telegram import Bot

# Fallback import — private config takes priority
try:
    from private.config_live import TELEGRAM_TOKEN, CHAT_ID, OPENSEA_API_KEY, COLLECTIONS, MINT_COOLDOWN_MINUTES
except ImportError:
    from config import TELEGRAM_TOKEN, CHAT_ID, OPENSEA_API_KEY, COLLECTIONS, MINT_COOLDOWN_MINUTES

bot = Bot(token=TELEGRAM_TOKEN)

# Track last seen mint timestamp per collection — avoids duplicate alerts
last_seen = {}

# Track last alert time per collection — cooldown system
mint_last_alerted = {}

async def send(msg):
    await bot.send_message(chat_id=CHAT_ID, text=msg)

def get_recent_mints(slug, since_timestamp):
    """
    Use OpenSea collection events endpoint with event_type=mint
    This is the correct endpoint for tracking mints by collection slug
    """
    url = f"https://api.opensea.io/api/v2/events/collection/{slug}"
    headers = {"x-api-key": OPENSEA_API_KEY}
    params = {
        "event_type": "mint",
        "after": int(since_timestamp),
        "limit": 20,
    }
    res = requests.get(url, headers=headers, params=params, timeout=10)
    res.raise_for_status()
    return res.json().get("asset_events", [])

def check_mints():
    print("[Mint] Checking for new mints...")
    for col in COLLECTIONS:
        contract = col["contract"]
        slug = col["slug"]
        try:
            since = last_seen.get(
                contract,
                (datetime.now(timezone.utc) - timedelta(minutes=2)).timestamp()
            )
            mints = get_recent_mints(slug, since)

            if mints:
                latest_ts = max(
                    m.get("event_timestamp", since) for m in mints
                )
                last_seen[contract] = latest_ts

                for mint in mints:
                    # ── COOLDOWN CHECK ──────────────────────────────
                    now = datetime.now(timezone.utc).timestamp()
                    last = mint_last_alerted.get(contract, 0)

                    if (now - last) < MINT_COOLDOWN_MINUTES * 60:
                        print(f"[Mint] Cooldown active for {col['name']}, skipping alert")
                        continue

                    mint_last_alerted[contract] = now
                    # ────────────────────────────────────────────────

                    nft = mint.get("nft", {})
                    token_id = nft.get("identifier", "?")
                    to_addr = mint.get("to_address", "?")
                    short_addr = f"{to_addr[:6]}...{to_addr[-4:]}" if len(to_addr) > 10 else to_addr

                    asyncio.run(send(
                        f"🟢 New Mint Detected!\n"
                        f"Collection: {col['name']}\n"
                        f"Token ID: #{token_id}\n"
                        f"Minted by: {short_addr}\n"
                        f"🔗 https://opensea.io/assets/ethereum/{contract}/{token_id}"
                    ))
                    print(f"[Mint] {col['name']} Token #{token_id} minted by {short_addr}")
            else:
                print(f"[Mint] No new mints for {col['name']}")
                last_seen[contract] = datetime.now(timezone.utc).timestamp()

        except Exception as e:
            print(f"[Mint Error] {col['name']}: {e}")