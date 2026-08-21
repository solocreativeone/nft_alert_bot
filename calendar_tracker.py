import asyncio
import re
from datetime import datetime, timezone
from collections import deque
from bs4 import BeautifulSoup
from curl_cffi import requests

import dedup
from notifier import asend, asend_photo, escape_html, download_image_bytes

# Fallback import - private config takes priority
try:
    from private.config_live import OPENSEA_API_KEY
except ImportError:
    from config import OPENSEA_API_KEY

# Bounded cache for the OpenSea new-collection path (keyed by slug).
# The NFTCalendar path uses the shared `dedup` module so it cooperates
# with live_drops.py and neither double-alerts the same drop link.
MAX_ALERTED_DROPS = 10000
alerted_drops_set = set()
alerted_drops_deque = deque(maxlen=MAX_ALERTED_DROPS)

# Junk filters — skip collections that match these
JUNK_NAMES = ["test", "miant", "spam", "airdrop", "fake", "scam"]
MIN_NAME_LENGTH = 3

# ==========================================
#               HELPER FUNCTIONS
# ==========================================

def is_junk(name, slug_or_url):
    """Filter out test collections, unnamed contracts, and spam."""
    if not name:
        return True
    if name.startswith("0x") and len(name) > 10:
        return True
    if len(name.strip()) < MIN_NAME_LENGTH:
        return True
    
    name_lower = name.lower()
    if any(kw in name_lower for kw in JUNK_NAMES):
        return True
    return False


#OPENSEA CONTROLLER            


def get_opensea_drops():
    headers = {
        "accept": "application/json",
        "x-api-key": OPENSEA_API_KEY
    }
    url = "https://api.opensea.io/api/v2/collections"
    params = {
        "chain": "ethereum",
        "order_by": "created_date",
        "limit": 25,
    }
    # Using curl_cffi requests for consistency and bypass capabilities
    res = requests.get(url, headers=headers, params=params, timeout=15)
    res.raise_for_status()
    return res.json().get("collections", [])

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


#NFTCALENDAR SCRAPER              


def get_nft_calendar_drops():
    """Scrapes upcoming mints from nftcalendar.io bypassing Cloudflare."""
    url = "https://nftcalendar.io/events/"
    
    # Impersonate Chrome 110 to slip past Cloudflare Turnstile/JS checks
    res = requests.get(
        url, 
        impersonate="chrome110", 
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        },
        timeout=15
    )
    res.raise_for_status()
    
    soup = BeautifulSoup(res.text, "html.parser")
    # Finding the card elements on NFTCalendar
    events = soup.find_all("article") or soup.select(".event-card, [class*='event']")
    
    # Fallback to Livewire event cards if standard elements aren't found or don't contain events
    if not events or len(events) < 5 or not any(e.find("h3") or e.find("h2") for e in events):
        livewire_events = [div for div in soup.find_all("div", attrs={"wire:key": True}) if div.find("h2") or div.find("h3")]
        if livewire_events:
            events = livewire_events
            
    parsed_drops = []
    for event in events:
        try:
            name_tag = event.find("h3") or event.find("h2")
            if not name_tag:
                continue
            name = name_tag.get_text(strip=True)
            
            # Use link path as the unique drop key
            link_tag = name_tag.find("a") or event.find("a")
            relative_link = link_tag["href"] if link_tag else ""
            full_link = f"https://nftcalendar.io{relative_link}" if relative_link.startswith("/") else relative_link
            
            date_tag = event.find("time") or event.select_one("[class*='date'], [class*='time']")
            date_text = date_tag.get_text(strip=True) if date_tag else ""
            if not date_text:
                for div in event.find_all("div"):
                    classes = div.get("class", [])
                    if any("date" in c or "time" in c for c in classes):
                        date_text = div.get_text(strip=True)
                        break
                if not date_text:
                    for div in event.find_all("div"):
                        txt = div.get_text(strip=True)
                        if re.match(r'^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)', txt):
                            date_text = txt
                            break
            if not date_text:
                date_text = "Upcoming"
            
            desc_tag = event.find("p") or event.select_one("[class*='desc'], [class*='text']")
            description = desc_tag.get_text(strip=True)[:120] if desc_tag else ""
            
            img_tag = event.find("img")
            img_url = None
            if img_tag:
                for attr in ["data-lazy-src", "data-src", "src"]:
                    val = img_tag.get(attr)
                    if val:
                        val = val.strip()
                        if val.startswith("data:"):
                            continue
                        img_url = val
                        break
            if img_url:
                if img_url.startswith("//"):
                    img_url = f"https:{img_url}"
                elif img_url.startswith("/"):
                    img_url = f"https://nftcalendar.io{img_url}"
                elif not img_url.startswith("http"):
                    img_url = f"https://nftcalendar.io/{img_url}"

            parsed_drops.append({
                "name": name,
                "link": full_link,
                "date": date_text,
                "description": description,
                "image": img_url
            })
        except Exception:
            continue
            
    return parsed_drops

# ==========================================
#             MAIN CHECK LOOP               
# ==========================================

# CORRECTED DEFINITION: Standard asynchronous declaration
async def check_calendar():
    print("[Calendar] Starting search cycle...")
    dedup.reset_if_new_day()
    messages_to_send = []
    total_alerted = 0

    # ---------------------------------------
    # Part A: Check OpenSea Collections
    # ---------------------------------------
    try:
        collections = await asyncio.to_thread(get_opensea_drops)
        for col in collections:
            slug = col.get("collection", "")
            name = col.get("name", slug)
            created = col.get("created_date", "")

            if not slug or slug in alerted_drops_set:
                continue
            if not is_within_age(created, max_hours=72) or is_junk(name, slug):
                continue

            supply = col.get("total_supply", "?")
            description = col.get("description", "")
            clean_desc = re.sub(r"<[^>]+>", "", description).strip()[:120] if description else ""

            # Update bounded deduplicator safely
            if len(alerted_drops_deque) == MAX_ALERTED_DROPS:
                alerted_drops_set.discard(alerted_drops_deque.popleft())
            alerted_drops_set.add(slug)
            alerted_drops_deque.append(slug)
            total_alerted += 1

            msg = (
                f"🆕 <b>New Collection on OpenSea!</b>\n"
                f"<b>Name:</b> {escape_html(name)}\n"
                f"<b>Supply:</b> {escape_html(supply)}\n"
            )
            if clean_desc:
                msg += f"<i>About:</i> {escape_html(clean_desc)}\n"
            msg += f'<a href="https://opensea.io/collection/{slug}">OpenSea Collection</a>'
            
            image_url = col.get("image_url") or col.get("featured_image_url") or None
            messages_to_send.append({"text": msg, "image": image_url})

    except Exception as e:
        print(f"[Calendar Error] OpenSea: {e}")

    # ---------------------------------------
    # Part B: Check NFTCalendar Events
    # ---------------------------------------
    try:
        calendar_events = await asyncio.to_thread(get_nft_calendar_drops)
        for event in calendar_events:
            name = event["name"]
            link = event["link"]
            date_str = event["date"]
            desc = event["description"]
            image_url = event.get("image")

            # Use the URL link as the unique identifier — shared with live_drops
            # via the dedup module so the same drop isn't alerted by both jobs.
            if not link or dedup.already_alerted(link):
                continue
            if is_junk(name, link):
                continue

            dedup.mark_alerted(link)
            total_alerted += 1

            msg = (
                f"📅 <b>Upcoming Mint (NFTCalendar)</b>\n"
                f"<b>Name:</b> {escape_html(name)}\n"
                f"<b>Date:</b> {escape_html(date_str)}\n"
            )
            if desc:
                msg += f"<i>About:</i> {escape_html(desc)}...\n"
            msg += f'<a href="{escape_html(link)}">View Drop Details on NFTCalendar</a>'
            messages_to_send.append({"text": msg, "image": image_url})

    except Exception as e:
        print(f"[Calendar Error] NFTCalendar: {e}")

    # ---------------------------------------
    # Part C: Dispatch Notifications
    # ---------------------------------------
    if messages_to_send:
        for msg_item in messages_to_send:
            text = msg_item["text"]
            image = msg_item.get("image")
            
            sent = False
            if image:
                try:
                    image_stream = await download_image_bytes(image)
                    await asend_photo(image_stream, caption=text, parse_mode="HTML")
                    sent = True
                except Exception as photo_err:
                    print(f"[Calendar] Failed to download or send photo ({image}): {photo_err}. Falling back to send_message.")

            if not sent:
                try:
                    await asend(text, parse_mode="HTML")
                except Exception as msg_err:
                    print(f"[Calendar] Failed to send Telegram alert: {msg_err}")
            
            await asyncio.sleep(0.1) # Small sleep to respect Telegram API rate limits

    if total_alerted == 0:
        print("[Calendar] Cycle finished: No new drops detected.")
    else:
        print(f"[Calendar] Cycle finished: ✅ Sent {total_alerted} new alert(s)")