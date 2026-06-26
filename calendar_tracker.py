import requests
import asyncio
from datetime import datetime, timezone
from telegram import Bot
from bs4 import BeautifulSoup

# Fallback import — private config takes priority
try:
    from private.config_live import TELEGRAM_TOKEN, CHAT_ID
except ImportError:
    from config import TELEGRAM_TOKEN, CHAT_ID

bot = Bot(token=TELEGRAM_TOKEN)

# Track drops we've already alerted on
alerted_drops = set()

CALENDAR_URL = "https://nftcalendar.io/b/ethereum/"

async def send(msg):
    await bot.send_message(chat_id=CHAT_ID, text=msg)

def get_upcoming_drops():
    """
    Scrapes nftcalendar.io for upcoming Ethereum drops.
    Returns a list of dicts with name, date, and link.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    res = requests.get(CALENDAR_URL, headers=headers, timeout=15)
    res.raise_for_status()

    soup = BeautifulSoup(res.text, "html.parser")
    drops = []

    # nftcalendar.io uses event cards with class "event-card"
    cards = soup.find_all("div", class_="event-item")

    for card in cards:
        try:
            # Extract name
            name_tag = card.find("h3") or card.find("h2") or card.find("strong")
            name = name_tag.get_text(strip=True) if name_tag else "Unknown"

            # Extract date
            date_tag = card.find("time") or card.find(class_=lambda x: x and "date" in x.lower())
            date_str = date_tag.get_text(strip=True) if date_tag else "TBA"

            # Extract link
            link_tag = card.find("a", href=True)
            link = f"https://nftcalendar.io{link_tag['href']}" if link_tag else CALENDAR_URL

            drops.append({
                "name": name,
                "date": date_str,
                "link": link,
            })
        except Exception:
            continue

    return drops

def check_calendar():
    print("[Calendar] Checking for upcoming NFT drops...")
    try:
        drops = get_upcoming_drops()

        if not drops:
            print("[Calendar] No upcoming drops found")
            return

        print(f"[Calendar] Found {len(drops)} upcoming drop(s)")

        for drop in drops:
            # Use name as unique key to avoid duplicate alerts
            drop_key = drop["name"].lower().replace(" ", "-")

            if drop_key in alerted_drops:
                continue

            alerted_drops.add(drop_key)

            asyncio.run(send(
                f"📅 Upcoming NFT Drop!\n"
                f"Name: {drop['name']}\n"
                f"Date: {drop['date']}\n"
                f"Chain: Ethereum\n"
                f"🔗 {drop['link']}"
            ))
            print(f"[Calendar] 📅 Alerted: {drop['name']} — {drop['date']}")

    except Exception as e:
        print(f"[Calendar Error] {e}")