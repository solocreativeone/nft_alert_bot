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

NFTCALENDAR_ETH_URL = "https://nftcalendar.io/b/ethereum/"

async def send(msg):
    await bot.send_message(chat_id=CHAT_ID, text=msg)

def reset_if_new_day():
    global alerted_live_drops, last_reset_date
    today = datetime.now(timezone.utc).date()
    if last_reset_date != today:
        alerted_live_drops = set()
        last_reset_date = today
        print("[LiveDrops] Daily reset — cleared cache")

def get_ethereum_drops():
    """
    Scrape NFTCalendar's Ethereum drops page.
    Returns list of dicts with name, date, description, link.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        res = requests.get(NFTCALENDAR_ETH_URL, headers=headers, timeout=15)

        if res.status_code == 403:
            print("[LiveDrops] NFTCalendar returned 403")
            return []

        res.raise_for_status()
        html = res.text

        # Extract event links
        event_pattern = r'href="(https://nftcalendar\.io/event/([a-z0-9\-]+)/)"'
        event_matches = re.findall(event_pattern, html)

        # Extract names from h2 tags
        name_pattern = r'<h2[^>]*>\s*<a[^>]*>\s*([^<]+?)\s*</a>\s*</h2>'
        names = re.findall(name_pattern, html)

        # Extract dates
        date_pattern = r'(\w{3}\s+\d{1,2},\s+202\d)'
        dates = re.findall(date_pattern, html)

        # Extract descriptions
        desc_pattern = r'verified\s*([\w][^<\[]{30,200}?)\s*\[Read More\]|</a>\s*\n\n([\w][^<\[]{30,200}?)\s*\[Read More\]'
        raw_descs = re.findall(desc_pattern, html)
        descriptions = [d[0].strip() or d[1].strip() for d in raw_descs]

        # Deduplicate links
        seen = set()
        unique = []
        for link, slug in event_matches:
            if slug not in seen:
                seen.add(slug)
                unique.append((link, slug))

        drops = []
        for i, (link, slug) in enumerate(unique[:20]):
            drops.append({
                "name": names[i].strip() if i < len(names) else slug,
                "slug": slug,
                "link": link,
                "date": dates[i * 2].strip() if i * 2 < len(dates) else "TBA",
                "description": descriptions[i][:150] if i < len(descriptions) else "",
            })

        return drops

    except requests.exceptions.Timeout:
        print("[LiveDrops] NFTCalendar request timed out")
        return []
    except Exception as e:
        print(f"[LiveDrops] Error: {e}")
        return []

def get_live_drops_summary():
    """
    Returns formatted list of upcoming Ethereum drops.
    Used by /live Telegram command.
    """
    drops = get_ethereum_drops()
    if not drops:
        return "❌ Could not fetch drops from NFTCalendar — try again shortly."

    lines = ["🔥 Upcoming Ethereum NFT Drops (NFTCalendar):\n"]
    for i, drop in enumerate(drops[:10], 1):
        lines.append(
            f"{i}. {drop['name']}\n"
            f"   Date: {drop['date']}\n"
            f"   🔗 {drop['link']}\n"
        )

    return "\n".join(lines)

def check_live_drops():
    print("[LiveDrops] Checking NFTCalendar Ethereum drops...")
    reset_if_new_day()

    drops = get_ethereum_drops()
    print(f"[LiveDrops] Found {len(drops)} Ethereum drops")

    messages_to_send = []
    total_alerted = 0

    for drop in drops:
        if drop["slug"] in alerted_live_drops:
            continue

        alerted_live_drops.add(drop["slug"])
        total_alerted += 1

        msg = (
            f"🔥 Upcoming ETH Mint!\n"
            f"Name: {drop['name']}\n"
            f"Date: {drop['date']}\n"
        )
        if drop["description"]:
            msg += f"About: {drop['description']}\n"
        msg += f"🔗 {drop['link']}"

        messages_to_send.append(msg)
        print(f"[LiveDrops] 🔥 Queued: {drop['name']}")

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
        print("[LiveDrops] No new drops to alert on")
    else:
        print(f"[LiveDrops] ✅ Sent {total_alerted} alert(s)")