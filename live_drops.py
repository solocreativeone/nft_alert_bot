import requests
import asyncio
import re
from datetime import datetime, timezone
from telegram import Bot

try:
    from private.config_live import TELEGRAM_TOKEN, CHAT_ID, OPENSEA_API_KEY
except ImportError:
    from config import TELEGRAM_TOKEN, CHAT_ID, OPENSEA_API_KEY

bot = Bot(token=TELEGRAM_TOKEN)

# Track slugs already alerted — resets daily
alerted_live_drops = set()
last_reset_date = None

DROPS_URL = "https://opensea.io/drops"

async def send(msg):
    await bot.send_message(chat_id=CHAT_ID, text=msg)

def reset_if_new_day():
    """Clear the alerted set once per day so new alerts fire fresh each day."""
    global alerted_live_drops, last_reset_date
    today = datetime.now(timezone.utc).date()
    if last_reset_date != today:
        alerted_live_drops = set()
        last_reset_date = today
        print("[LiveDrops] Daily reset — cleared alerted drops cache")

def get_live_upcoming_mints():
    """
    Scrape OpenSea's main drops page.
    NOTE: Relies on OpenSea HTML structure — may break if they update their layout.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        res = requests.get(DROPS_URL, headers=headers, timeout=15)

        if res.status_code == 403:
            print("[LiveDrops] OpenSea blocked the request — trying alternate URL")
            return get_live_mints_via_api()

        res.raise_for_status()

        # Extract collection slugs from links
        pattern = r'/collection/([a-z0-9\-]+)(?:/overview)?["\s]'
        matches = re.findall(pattern, res.text)

        # Deduplicate while preserving order
        seen = set()
        slugs = []
        for slug in matches:
            if slug not in seen and len(slug) > 3:
                seen.add(slug)
                slugs.append(slug)

        return slugs[:20]

    except requests.exceptions.Timeout:
        print("[LiveDrops] Request timed out")
        return []
    except Exception as e:
        print(f"[LiveDrops] Error fetching drops page: {e}")
        return []

def get_live_mints_via_api():
    """
    Fallback: use OpenSea API to find collections with recent activity.
    Filters for collections with actual supply > 0.
    """
    headers = {"x-api-key": OPENSEA_API_KEY}
    url = "https://api.opensea.io/api/v2/collections"
    params = {
        "chain": "ethereum",
        "order_by": "created_date",
        "limit": 25,
    }
    try:
        res = requests.get(url, headers=headers, params=params, timeout=15)
        res.raise_for_status()
        collections = res.json().get("collections", [])
        return [c.get("collection", "") for c in collections if c.get("total_supply", 0) > 0]
    except Exception as e:
        print(f"[LiveDrops] API fallback error: {e}")
        return []

def get_collection_details(slug):
    """Fetch collection details from OpenSea API."""
    headers = {"x-api-key": OPENSEA_API_KEY}
    url = f"https://api.opensea.io/api/v2/collections/{slug}"

    try:
        res = requests.get(url, headers=headers, timeout=10)

        if res.status_code == 429:
            print(f"[LiveDrops] Rate limited fetching {slug}")
            return None
        if res.status_code != 200:
            return None

        data = res.json()
        supply = data.get("total_supply", 0)

        # Skip collections with zero supply — they're shells/junk
        if not supply or supply == 0:
            return None

        return {
            "name": data.get("name", slug),
            "slug": slug,
            "description": (data.get("description") or "")[:120],
            "total_supply": supply,
        }
    except Exception as e:
        print(f"[LiveDrops] Error fetching details for {slug}: {e}")
        return None

def get_live_drops_summary():
    """
    Returns a formatted summary of current live/upcoming mints.
    Used by the /live Telegram command.
    """
    slugs = get_live_upcoming_mints()
    if not slugs:
        return "No live or upcoming mints found on OpenSea right now."

    lines = ["🔥 Live & Upcoming Mints on OpenSea:\n"]
    count = 0

    for slug in slugs:
        details = get_collection_details(slug)
        if not details:
            continue

        count += 1
        lines.append(
            f"{count}. {details['name']}\n"
            f"   Supply: {details['total_supply']}\n"
            f"   🔗 https://opensea.io/collection/{slug}\n"
        )

        if count >= 10:
            break

    if count == 0:
        return "Found listings but couldn't fetch details — try again shortly."

    return "\n".join(lines)

def check_live_drops():
    print("[LiveDrops] Checking OpenSea Live & Upcoming Mints...")

    # Reset alerted set once per day
    reset_if_new_day()

    messages_to_send = []
    total_alerted = 0

    slugs = get_live_upcoming_mints()
    print(f"[LiveDrops] Found {len(slugs)} potential mints")

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