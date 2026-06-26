import requests
import asyncio
import xml.etree.ElementTree as ET
from telegram import Bot

# Fallback import — private config takes priority
try:
    from private.config_live import TELEGRAM_TOKEN, CHAT_ID
except ImportError:
    from config import TELEGRAM_TOKEN, CHAT_ID

bot = Bot(token=TELEGRAM_TOKEN)

# Track drops we've already alerted on
alerted_drops = set()

# RSS feed URLs
RSS_FEEDS = [
    {
        "name": "NFTCalendar",
        "url": "https://nftcalendar.io/feed/",
    },
    {
        "name": "NFT Evening",
        "url": "https://nftevening.com/feed/",
    },
]

# Must have at least one of these to be considered a drop
DROP_KEYWORDS = [
    "free mint", "public mint", "whitelist mint", "nft drop",
    "minting now", "mint date", "mint price", "presale",
    "allowlist", "nft launch", "collection drop", "mint opens",
    "upcoming mint", "nft release"
]

# Always skip if any of these appear
NEWS_KEYWORDS = [
    "weekly drop", "price drop", "drops since", "token airdrop",
    "hack", "stolen", "lawsuit", "validator", "stablecoin",
    "prediction", "analysis", "report", "regulation", "sec",
    "joins", "partnership", "raises", "funding", "etf",
    "plunges", "rallying", "outperform", "collapse", "airdrop",
    "hodler", "binance", "coinbase", "blackrock", "bybit"
]

def parse_rss(feed_url):
    headers = {"User-Agent": "Mozilla/5.0 (compatible; NFTAlertBot/1.0)"}
    res = requests.get(feed_url, headers=headers, timeout=15)
    res.raise_for_status()

    root = ET.fromstring(res.content)
    channel = root.find("channel")
    if not channel:
        return []

    items = []
    for item in channel.findall("item"):
        title = item.findtext("title", "").strip()
        link = item.findtext("link", "").strip()
        pub_date = item.findtext("pubDate", "").strip()
        description = item.findtext("description", "").strip()

        if title and link:
            items.append({
                "title": title,
                "link": link,
                "date": pub_date,
                "description": description[:200] if description else "",
            })

    return items

def is_drop_announcement(item):
    """Return True only for actual drop/mint announcements."""
    text = (item["title"] + " " + item["description"]).lower()

    # Skip news articles first
    if any(kw in text for kw in NEWS_KEYWORDS):
        return False

    # Must contain a strong drop keyword
    if not any(kw in text for kw in DROP_KEYWORDS):
        return False

    # Skip Solana-only or Bitcoin-only drops
    other_chains = ["solana", "polygon", "bitcoin ordinal"]
    eth_keywords = ["ethereum", "eth ", "erc-721", "erc721", "mainnet"]
    is_other_only = any(kw in text for kw in other_chains) and not any(kw in text for kw in eth_keywords)

    return not is_other_only

def check_calendar():
    print("[Calendar] Checking RSS feeds for upcoming NFT drops...")
    total_alerted = 0
    messages_to_send = []

    for feed in RSS_FEEDS:
        try:
            items = parse_rss(feed["url"])
            print(f"[Calendar] {feed['name']}: {len(items)} items — filtering...")

            for item in items:
                drop_key = item["link"]

                if drop_key in alerted_drops:
                    continue

                if not is_drop_announcement(item):
                    continue

                alerted_drops.add(drop_key)
                total_alerted += 1

                messages_to_send.append(
                    f"📅 Upcoming NFT Drop!\n"
                    f"Name: {item['title']}\n"
                    f"Date: {item['date']}\n"
                    f"Source: {feed['name']}\n"
                    f"🔗 {item['link']}"
                )
                print(f"[Calendar] 📅 Queued: {item['title']}")

        except Exception as e:
            print(f"[Calendar Error] {feed['name']}: {e}")

    # Send all messages in one async batch
    if messages_to_send:
        async def send_all():
            for msg in messages_to_send:
                try:
                    await bot.send_message(chat_id=CHAT_ID, text=msg)
                except Exception as e:
                    print(f"[Calendar] Failed to send message: {e}")

        try:
            asyncio.run(send_all())
        except Exception as e:
            print(f"[Calendar] Telegram send error: {e}")

    if total_alerted == 0:
        print("[Calendar] No new drop announcements found")
    else:
        print(f"[Calendar] ✅ Sent {total_alerted} drop alert(s)")