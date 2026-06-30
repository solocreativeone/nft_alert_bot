import requests
import asyncio
import re
from telegram import Bot

# Fallback import — private config takes priority
try:
    from private.config_live import TELEGRAM_TOKEN, CHAT_ID, OPENSEA_API_KEY
except ImportError:
    from config import TELEGRAM_TOKEN, CHAT_ID, OPENSEA_API_KEY

bot = Bot(token=TELEGRAM_TOKEN)

# Track slugs already alerted
alerted_live_drops = set()

DROPS_URL = "https://opensea.io/drops/upcoming"

async def send(msg):
    await bot.send_message(chat_id=CHAT_ID, text=msg)

def get_live_upcoming_mints():
    """
    Scrape OpenSea's curated Live & Upcoming Mints section.
    Extracts collection slugs from the page, then fetches details via API.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    res = requests.get(DROPS_URL, headers=headers, timeout=15)
    res.raise_for_status()

    # Extract collection slugs from links like /collection/slug-name/overview
    pattern = r'/collection/([a-z0-9\-]+)(?:/overview)?'
    matches = re.findall(pattern, res.text)

    # Deduplicate while preserving order
    seen = set()
    slugs = []
    for slug in matches:
        if slug not in seen and len(slug) > 2:
            seen.add(slug)
            slugs.append(slug)

    return slugs[:15]  # Curated section is small, cap at 15

def get_collection_details(slug):
    """Fetch collection details from OpenSea API for a given slug."""
    headers = {"x-api-key": OPENSEA_API_KEY}
    url = f"https://api.opensea.io/api/v2/collections/{slug}"
    res = requests.get(url, headers=headers, timeout=10)

    if res.status_code != 200:
        return None

    data = res.json()
    return {
        "name": data.get("name", slug),
        "slug": slug,
        "description": (data.get("description") or "")[:120],
        "total_supply": data.get("total_supply", "?"),
    }

def check_live_drops():
    print("[LiveDrops] Checking OpenSea Live & Upcoming Mints...")
    messages_to_send = []
    total_alerted = 0

    try:
        slugs = get_live_upcoming_mints()
        print(f"[LiveDrops] Found {len(slugs)} curated mints on OpenSea")

        for slug in slugs:
            if slug in alerted_live_drops:
                continue

            details = get_collection_details(slug)
            if not details:
                continue

            alerted_live_drops.add(slug)
            total_alerted += 1

            msg = (
                f"🔥 Live/Upcoming Mint on OpenSea!\n"
                f"Name: {details['name']}\n"
                f"Supply: {details['total_supply']}\n"
            )
            if details["description"]:
                msg += f"About: {details['description']}\n"
            msg += f"🔗 https://opensea.io/collection/{slug}"

            messages_to_send.append(msg)
            print(f"[LiveDrops] 🔥 Queued: {details['name']}")

    except Exception as e:
        print(f"[LiveDrops Error] {e}")

    # Send all messages
    if messages_to_send:
        async def send_all():
            for msg in messages_to_send:
                try:
                    await bot.send_message(chat_id=CHAT_ID, text=msg)
                except Exception as e:
                    print(f"[LiveDrops] Failed to send: {e}")

        try:
            asyncio.run(send_all())
        except Exception as e:
            print(f"[LiveDrops] Telegram error: {e}")

    if total_alerted == 0:
        print("[LiveDrops] No new live/upcoming mints to alert on")
    else:
        print(f"[LiveDrops] ✅ Sent {total_alerted} alert(s)")