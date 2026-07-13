import requests
import asyncio
import re
from datetime import datetime, timezone
from telegram import Bot

try:
    from private.config_live import TELEGRAM_TOKEN, CHAT_ID
except ImportError:
    from config import TELEGRAM_TOKEN, CHAT_ID

bot = Bot(token=TELEGRAM_TOKEN)

# Track slugs already alerted — resets daily
alerted_live_drops = set()
last_reset_date = None

LIVE_MINTS_URL = "https://nftcalendar.io/mints/"

async def send(msg):
    await bot.send_message(chat_id=CHAT_ID, text=msg)

def reset_if_new_day():
    """Clear alerted set once per day."""
    global alerted_live_drops, last_reset_date
    today = datetime.now(timezone.utc).date()
    if last_reset_date != today:
        alerted_live_drops = set()
        last_reset_date = today
        print("[LiveDrops] Daily reset — cleared alerted drops cache")

def get_live_mints():
    """
    Scrape NFTCalendar's live mints page — real blockchain data,
    not OpenSea HTML. Returns collections actively minting right now.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        res = requests.get(LIVE_MINTS_URL, headers=headers, timeout=15)

        if res.status_code == 403:
            print("[LiveDrops] NFTCalendar returned 403 — blocked")
            return []

        res.raise_for_status()
        html = res.text

        # Extract event links and names from the table
        # Pattern: /event/slug/ followed by collection name
        row_pattern = r'href="(https://nftcalendar\.io/event/([a-z0-9\-]+)/)"[^>]*>\s*([^\n<]+?)(?:\s+First minted on ([^\]]+))?\]'
        rows = re.findall(row_pattern, html)

        # Extract latest mint times
        time_pattern = r'(\d{2}\s+\w{3}\s+202\d\s+\d{2}:\d{2}:\d{2})'
        times = re.findall(time_pattern, html)

        # Extract minter counts and total mints
        stats_pattern = r'(\d+)\s*\([\d.]+%\)\s*(\d+)'
        stats = re.findall(stats_pattern, html)

        mints = []
        for i, (link, slug, name_raw, first_minted) in enumerate(rows[:20]):
            name = name_raw.strip()
            last_mint = times[i] if i < len(times) else "Unknown"
            unique_minters = stats[i][0] if i < len(stats) else "?"
            total_mints = stats[i][1] if i < len(stats) else "?"

            mints.append({
                "name": name,
                "slug": slug,
                "link": link,
                "last_mint": last_mint,
                "first_minted": first_minted.strip() if first_minted else "Unknown",
                "unique_minters": unique_minters,
                "total_mints": total_mints,
            })

        return mints

    except requests.exceptions.Timeout:
        print("[LiveDrops] NFTCalendar request timed out")
        return []
    except Exception as e:
        print(f"[LiveDrops] Error: {e}")
        return []

def get_live_drops_summary():
    """
    Returns formatted summary of currently minting collections.
    Used by /live Telegram command.
    """
    mints = get_live_mints()
    if not mints:
        return "No live mints found on NFTCalendar right now."

    lines = ["🔥 Currently Minting — NFTCalendar Live Tracker:\n"]
    for i, mint in enumerate(mints[:10], 1):
        lines.append(
            f"{i}. {mint['name']}\n"
            f"   Last mint: {mint['last_mint']}\n"
            f"   Minters: {mint['unique_minters']} | Total mints: {mint['total_mints']}\n"
            f"   🔗 {mint['link']}\n"
        )

    return "\n".join(lines)

def check_live_drops():
    print("[LiveDrops] Checking NFTCalendar live mints...")
    reset_if_new_day()

    mints = get_live_mints()
    print(f"[LiveDrops] Found {len(mints)} actively minting collections")

    messages_to_send = []
    total_alerted = 0

    for mint in mints:
        slug = mint["slug"]

        if slug in alerted_live_drops:
            continue

        alerted_live_drops.add(slug)
        total_alerted += 1

        messages_to_send.append(
            f"🔥 Live Mint Alert!\n"
            f"Collection: {mint['name']}\n"
            f"First minted: {mint['first_minted']}\n"
            f"Last mint: {mint['last_mint']}\n"
            f"Unique minters: {mint['unique_minters']}\n"
            f"Total mints: {mint['total_mints']}\n"
            f"🔗 {mint['link']}"
        )
        print(f"[LiveDrops] 🔥 Queued: {mint['name']} — {mint['total_mints']} mints")

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
        print("[LiveDrops] No new live mints to alert on")
    else:
        print(f"[LiveDrops] ✅ Sent {total_alerted} alert(s)")