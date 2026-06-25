import requests
import asyncio
from telegram import Bot

# Fallback import — private config takes priority
try:
    from private.config_live import TELEGRAM_TOKEN, CHAT_ID, OPENSEA_API_KEY, COLLECTIONS
except ImportError:
    from config import TELEGRAM_TOKEN, CHAT_ID, OPENSEA_API_KEY, COLLECTIONS

bot = Bot(token=TELEGRAM_TOKEN)
floor_last_alerted = {}

async def send(msg):
    await bot.send_message(chat_id=CHAT_ID, text=msg)

def get_floor(slug):
    url = f"https://api.opensea.io/api/v2/collections/{slug}/stats"
    headers = {"x-api-key": OPENSEA_API_KEY}
    res = requests.get(url, headers=headers, timeout=10)
    res.raise_for_status()
    data = res.json()
    return float(data["total"]["floor_price"])

def check_floors():
    print("[Floor] Running floor price check...")
    for col in COLLECTIONS:
        try:
            floor = get_floor(col["slug"])
            print(f"[Floor] {col['name']}: {floor} ETH")

            if floor < col["floor_alert_low"]:
                asyncio.run(send(
                    f"🚨 Floor Drop Alert!\n"
                    f"Collection: {col['name']}\n"
                    f"Floor: {floor} ETH\n"
                    f"⬇️ Below your low target of {col['floor_alert_low']} ETH"
                ))
            elif floor > col["floor_alert_high"]:
                asyncio.run(send(
                    f"🚀 Floor Pump Alert!\n"
                    f"Collection: {col['name']}\n"
                    f"Floor: {floor} ETH\n"
                    f"⬆️ Above your high target of {col['floor_alert_high']} ETH"
                ))

        except Exception as e:
            print(f"[Floor Error] {col['name']}: {e}")