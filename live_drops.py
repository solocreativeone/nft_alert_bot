from curl_cffi import requests
from bs4 import BeautifulSoup
import asyncio
from datetime import datetime, timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import dedup
from notifier import asend, asend_photo, download_image_bytes, escape_html

# NFTCalendar chain URLs — add more chains here as needed
NFTCALENDAR_CHAINS = {
    "ethereum": "https://nftcalendar.io/b/ethereum/",
    "polygon":  "https://nftcalendar.io/b/polygon/",
    "base":     "https://nftcalendar.io/b/base/",
    "solana":   "https://nftcalendar.io/b/solana/",
}

# Junk filters. Moved here from calendar_tracker.py, which was deleted as dead
# code: nothing imported it and check_calendar() was never scheduled. These two
# helpers were the only part worth keeping, and this module is the live consumer
# of NFTCalendar data, so they now filter results that actually reach a user.
JUNK_NAMES = ["test", "miant", "spam", "airdrop", "fake", "scam"]
MIN_NAME_LENGTH = 3


def is_junk(name, slug_or_url):
    """Filter out test collections, unnamed contracts, and spam."""
    if not name:
        return True
    if name.startswith("0x") and len(name) > 10:
        return True
    if len(name.strip()) < MIN_NAME_LENGTH:
        return True
    return any(kw in name.lower() for kw in JUNK_NAMES)


def is_within_age(created, max_hours=72):
    """Return True if the collection was created within max_hours."""
    if not created:
        return False
    try:
        created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        age_hours = (datetime.now(timezone.utc) - created_dt).total_seconds() / 3600
        return age_hours <= max_hours
    except Exception:
        return False


def scrape_nftcalendar(url, chain):
    """
    Scrape an NFTCalendar chain page.
    Returns list of dicts with name, date, description, link, chain.
    """
    try:
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

        if res.status_code == 403:
            print(f"[LiveDrops] NFTCalendar returned 403 for {chain}")
            return []

        res.raise_for_status()

        soup = BeautifulSoup(res.text, "html.parser")
        h2s = [h2 for h2 in soup.find_all("h2") if "text-2xl" in "".join(h2.get("class", []))]

        drops = []
        for h2 in h2s:
            card = h2.find_parent("div", class_=lambda c: c and "w-full" in c and "px-2" in c)
            if not card:
                continue

            outer_card = card.parent

            # Link
            link_tag = h2.find_parent("a")
            relative_link = link_tag["href"] if link_tag else ""
            full_link = f"https://nftcalendar.io{relative_link}" if relative_link.startswith("/") else relative_link

            # Slug
            slug = relative_link.strip("/").split("/")[-1] if relative_link else ""
            if not slug:
                continue

            # Date
            date_div = outer_card.find("div", class_=lambda c: c and "py-2" in c and "text-black" in c)
            date_text = " ".join(date_div.get_text().split()) if date_div else "TBA"

            # Description
            desc_p = outer_card.find("p")
            desc_text = desc_p.get_text(strip=True) if desc_p else ""

            # Image — NFTCalendar puts a thumbnail inside the card
            img_tag = outer_card.find("img")
            image_url = img_tag.get("src") or img_tag.get("data-src") if img_tag else None

            name = h2.get_text(strip=True)
            if is_junk(name, full_link):
                continue

            drops.append({
                "name": name,
                "slug": slug,
                "link": full_link,
                "date": date_text,
                "description": desc_text[:150],
                "chain": chain,
                "image": image_url,
            })

        return drops

    except Exception as e:
        print(f"[LiveDrops] Error scraping {chain}: {e}")
        return []

def get_live_drops_summary(chain="ethereum"):
    """
    Returns formatted list of upcoming drops for a given chain.
    Used by the /live Telegram command.
    """
    url = NFTCALENDAR_CHAINS.get(chain)
    if not url:
        supported = ", ".join(NFTCALENDAR_CHAINS.keys())
        return f"❌ Unsupported chain. Supported: {supported}"

    drops = scrape_nftcalendar(url, chain)
    if not drops:
        return f"❌ Could not fetch drops for {chain} from NFTCalendar — try again shortly."

    lines = [f"🔥 Upcoming {chain.capitalize()} NFT Drops (NFTCalendar):\n"]
    for i, drop in enumerate(drops[:10], 1):
        lines.append(
            f"{i}. {drop['name']}\n"
            f"   Date: {drop['date']}\n"
            f"   🔗 {drop['link']}\n"
        )

    return "\n".join(lines)

async def check_live_drops():
    """
    Polls NFTCalendar for upcoming drops across all supported chains
    and sends Telegram alerts for new entries not yet seen.
    """
    print("[LiveDrops] Checking NFTCalendar drops across all chains...")
    dedup.reset_if_new_day()

    total_alerted = 0

    for chain, url in NFTCALENDAR_CHAINS.items():
        drops = await asyncio.to_thread(scrape_nftcalendar, url, chain)
        print(f"[LiveDrops] {chain.capitalize()}: {len(drops)} drop(s) found")

        for drop in drops:
            # Keyed on the full link so the same drop isn't alerted twice
            # (shared with calendar_tracker via the dedup module)
            key = drop["link"]
            if dedup.already_alerted(key):
                continue

            dedup.mark_alerted(key)
            total_alerted += 1

            chain_emoji = {
                "ethereum": "⟠",
                "polygon":  "🟣",
                "base":     "🔵",
                "solana":   "◎",
            }.get(chain, "🔗")

            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton(text="🗓️ View on NFTCalendar", url=drop["link"])]
            ])

            text = (
                f"🔥 <b>Upcoming Mint — {chain_emoji} {chain.capitalize()}</b>\n\n"
                f"<b>{escape_html(drop['name'])}</b>\n"
                f"📅 Date: <b>{escape_html(drop['date'])}</b>\n"
            )
            if drop["description"]:
                text += f"<i>{escape_html(drop['description'])}</i>\n"

            sent = False
            image_url = drop.get("image")
            if image_url:
                try:
                    img_bytes = await download_image_bytes(image_url)
                    await asend_photo(img_bytes, caption=text, parse_mode="HTML", reply_markup=reply_markup)
                    sent = True
                except Exception as e:
                    print(f"[LiveDrops] Photo send failed for {drop['name']}: {e} — falling back to text")

            if not sent:
                await asend(text, reply_markup=reply_markup)

            print(f"[LiveDrops] ✅ Sent: {drop['name']} [{chain}]")

    if total_alerted == 0:
        print("[LiveDrops] No new drops to alert on")
    else:
        print(f"[LiveDrops] ✅ Sent {total_alerted} alert(s) total")