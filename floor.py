import requests
import asyncio
import time
from datetime import datetime, timezone
from telegram import Bot
from watchlist import merge_with_config

try:
    from private.config_live import TELEGRAM_TOKEN, CHAT_ID, OPENSEA_API_KEY, COLLECTIONS, FLOOR_COOLDOWN_MINUTES
except ImportError:
    from config import TELEGRAM_TOKEN, CHAT_ID, OPENSEA_API_KEY, COLLECTIONS, FLOOR_COOLDOWN_MINUTES

bot = Bot(token=TELEGRAM_TOKEN)

# Cooldown tracker
floor_last_alerted = {}

async def send(msg):
    await bot.send_message(chat_id=CHAT_ID, text=msg)

def get_floor(slug):
    url = f"https://api.opensea.io/api/v2/collections/{slug}/stats"
    headers = {"x-api-key": OPENSEA_API_KEY}
    res = requests.get(url, headers=headers, timeout=10)

    # Handle rate limiting gracefully
    if res.status_code == 429:
        print(f"[Floor] Rate limited by OpenSea — backing off 60 seconds")
        time.sleep(60)
        return None

    res.raise_for_status()
    data = res.json()
    # Round to 4 decimal places to avoid float precision noise
    return round(float(data["total"]["floor_price"]), 4)

def check_floors():
    print("[Floor] Running floor price check...")

    all_collections = merge_with_config(COLLECTIONS)

    for col in all_collections:
        try:
            floor = get_floor(col["slug"])
            if floor is None:
                continue  # Rate limited — skip this cycle

            print(f"[Floor] {col['name']}: {floor} ETH")

            now = datetime.now(timezone.utc).timestamp()
            last = floor_last_alerted.get(col["slug"], 0)

            if (now - last) < FLOOR_COOLDOWN_MINUTES * 60:
                print(f"[Floor] Cooldown active for {col['name']}, skipping alert")
                continue

            if floor < col["floor_alert_low"]:
                asyncio.run(send(
                    f"🚨 Floor Drop Alert!\n"
                    f"Collection: {col['name']}\n"
                    f"Floor: {floor} ETH\n"
                    f"⬇️ Below your low target of {col['floor_alert_low']} ETH"
                ))
                floor_last_alerted[col["slug"]] = now

            elif floor > col["floor_alert_high"]:
                asyncio.run(send(
                    f"🚀 Floor Pump Alert!\n"
                    f"Collection: {col['name']}\n"
                    f"Floor: {floor} ETH\n"
                    f"⬆️ Above your high target of {col['floor_alert_high']} ETH"
                ))
                floor_last_alerted[col["slug"]] = now

        except Exception as e:
            print(f"[Floor Error] {col['name']}: {e}")