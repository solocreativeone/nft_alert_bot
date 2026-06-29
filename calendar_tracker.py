import requests
import asyncio
import re
from datetime import datetime, timezone
from telegram import Bot

# Fallback import — private config takes priority
try:
    from private.config_live import TELEGRAM_TOKEN, CHAT_ID, OPENSEA_API_KEY
except ImportError:
    from config import TELEGRAM_TOKEN, CHAT_ID, OPENSEA_API_KEY

bot = Bot(token=TELEGRAM_TOKEN)

# Track slugs we've already alerted on — slug is unique, prevents duplicates
alerted_drops = set()

# Junk filters — skip collections that match these
JUNK_NAMES = ["test", "miant", "spam", "airdrop", "fake", "scam"]
MIN_NAME_LENGTH = 3  # skip names that are too short

async def send(msg):
    await bot.send_message(chat_id=CHAT_ID, text=msg)

def get_opensea_drops():
    headers = {"x-api-key": OPENSEA_API_KEY}
    url = "https://api.opensea.io/api/v2/collections"
    params = {
        "chain": "ethereum",
        "order_by": "created_date",
        "limit": 25,
    }
    res = requests.get(url, headers=headers, params=params, timeout=15)
    res.raise_for_status()
    return res.json().get("collections", [])

def is_junk(name, slug):
    """Filter out test collections, unnamed contracts, and spam."""
    # Skip if name looks like a contract address
    if name.startswith("0x") and len(name) > 10:
        return True
    # Skip if name is too short
    if len(name.strip()) < MIN_NAME_LENGTH:
        return True
    # Skip if name contains junk keywords
    name_lower = name.lower()
    if any(kw in name_lower for kw in JUNK_NAMES):
        return True
    return False

def is_within_age(created, max_hours=72):
    """Return True if collection was created within max_hours."""
    if not created:
        return False
    try:
        created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        age_hours = (datetime.now(timezone.utc) - created_dt).total_seconds() / 3600
        return age_hours <= max_hours
    except Exception:
        return False

def check_calendar():
    print("[Calendar] Checking OpenSea for new collection launches...")
    messages_to_send = []
    total_alerted = 0

    try:
        collections = get_opensea_drops()
        print(f"[Calendar] OpenSea: {len(collections)} collections returned")

        for col in collections:
            slug = col.get("collection", "")
            name = col.get("name", slug)
            created = col.get("created_date", "")

            # Use slug as unique key — prevents duplicates
            drop_key = slug
            if not slug or drop_key in alerted_drops:
                continue

            # Skip old collections
            if not is_within_age(created, max_hours=72):
                continue

            # Skip junk
            if is_junk(name, slug):
                print(f"[Calendar] Skipping junk: {name}")
                continue

            supply = col.get("total_supply", "?")
            description = col.get("description", "")
            clean_desc = re.sub(r"<[^>]+>", "", description).strip()[:120] if description else ""

            alerted_drops.add(drop_key)
            total_alerted += 1

            msg = (
                f"🆕 New Collection on OpenSea!\n"
                f"Name: {name}\n"
                f"Supply: {supply}\n"
            )
            if clean_desc:
                msg += f"About: {clean_desc}\n"
            msg += f"🔗 https://opensea.io/collection/{slug}"

            messages_to_send.append(msg)
            print(f"[Calendar] 🆕 Queued: {name}")

    except Exception as e:
        print(f"[Calendar Error] OpenSea: {e}")

    # Send all messages
    if messages_to_send:
        async def send_all():
            for msg in messages_to_send:
                try:
                    await bot.send_message(chat_id=CHAT_ID, text=msg)
                except Exception as e:
                    print(f"[Calendar] Failed to send: {e}")

        try:
            asyncio.run(send_all())
        except Exception as e:
            print(f"[Calendar] Telegram error: {e}")

    if total_alerted == 0:
        print("[Calendar] No new drops to alert on")
    else:
        print(f"[Calendar] ✅ Sent {total_alerted} alert(s)")