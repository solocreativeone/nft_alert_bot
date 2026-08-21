import requests
import asyncio
from datetime import datetime, timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from watchlist import merge_with_config
from notifier import asend, asend_photo, download_image_bytes, escape_html

try:
    from private.config_live import OPENSEA_API_KEY, COLLECTIONS, FLOOR_COOLDOWN_MINUTES
except ImportError:
    from config import OPENSEA_API_KEY, COLLECTIONS, FLOOR_COOLDOWN_MINUTES

# Cooldown tracker
floor_last_alerted = {}

def get_floor_and_image(slug):
    headers = {"x-api-key": OPENSEA_API_KEY}

    # Get floor price
    stats_url = f"https://api.opensea.io/api/v2/collections/{slug}/stats"
    res = requests.get(stats_url, headers=headers, timeout=10)

    if res.status_code == 429:
        print(f"[Floor] Rate limited by OpenSea — skipping this cycle")
        return None, None

    res.raise_for_status()
    data = res.json()
    floor = round(float(data["total"]["floor_price"]), 4)

    # Get collection image
    col_url = f"https://api.opensea.io/api/v2/collections/{slug}"
    image_url = None
    try:
        col_res = requests.get(col_url, headers=headers, timeout=10)
        if col_res.status_code == 200:
            col_data = col_res.json()
            image_url = col_data.get("image_url") or col_data.get("featured_image_url")
    except Exception:
        pass  # Image is optional — don't block the alert

    return floor, image_url

async def send_floor_alert(col, floor, direction, image_url):
    """Send a floor alert, with collection image if available."""
    if direction == "low":
        headline = "🚨 <b>Floor Drop Alert!</b>"
        direction_line = f"⬇️ Below your target of <b>{col['floor_alert_low']} ETH</b>"
    else:
        headline = "🚀 <b>Floor Pump Alert!</b>"
        direction_line = f"⬆️ Above your target of <b>{col['floor_alert_high']} ETH</b>"

    chain = col.get("chain", "ethereum").capitalize()
    slug = col.get("slug", "")

    reply_markup = None
    if slug:
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(text="🌊 View on OpenSea", url=f"https://opensea.io/collection/{slug}")]
        ])

    text = (
        f"{headline}\n\n"
        f"<b>{escape_html(col['name'])}</b> [{chain}]\n"
        f"Floor: <b>{floor} ETH</b>\n"
        f"{direction_line}"
    )

    sent = False
    if image_url:
        try:
            image_bytes = await download_image_bytes(image_url)
            await asend_photo(image_bytes, caption=text, parse_mode="HTML", reply_markup=reply_markup)
            sent = True
        except Exception as e:
            print(f"[Floor] Photo send failed: {e} — falling back to text")

    if not sent:
        await asend(text, reply_markup=reply_markup)

async def check_floors():
    print("[Floor] Running floor price check...")

    all_collections = merge_with_config(COLLECTIONS)

    for col in all_collections:
        try:
            floor, image_url = await asyncio.to_thread(get_floor_and_image, col["slug"])
            if floor is None:
                continue  # Rate limited — skip this cycle

            print(f"[Floor] {col['name']}: {floor} ETH")

            now = datetime.now(timezone.utc).timestamp()
            last = floor_last_alerted.get(col["slug"], 0)

            if (now - last) < FLOOR_COOLDOWN_MINUTES * 60:
                print(f"[Floor] Cooldown active for {col['name']}, skipping alert")
                continue

            if floor < col["floor_alert_low"]:
                await send_floor_alert(col, floor, "low", image_url)
                floor_last_alerted[col["slug"]] = now

            elif floor > col["floor_alert_high"]:
                await send_floor_alert(col, floor, "high", image_url)
                floor_last_alerted[col["slug"]] = now

        except requests.exceptions.RequestException as e:
            print(f"[Floor Network Error] {col['name']}: {e}")
        except (KeyError, ValueError, TypeError) as e:
            print(f"[Floor Data Error] {col['name']}: {e}")
        except Exception as e:
            print(f"[Floor Unexpected Error] {col['name']}: {e}")
            raise
