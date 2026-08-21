import requests
import asyncio
from datetime import datetime, timezone, timedelta
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from watchlist import merge_with_config
from notifier import asend, asend_photo, download_image_bytes, escape_html

# Fallback import — private config takes priority
try:
    from private.config_live import OPENSEA_API_KEY, COLLECTIONS, MINT_COOLDOWN_MINUTES
except ImportError:
    from config import OPENSEA_API_KEY, COLLECTIONS, MINT_COOLDOWN_MINUTES

# Track last seen mint timestamp per collection
last_seen = {}

# Cooldown tracker
mint_last_alerted = {}

def get_recent_mints(slug, since_timestamp):
    url = f"https://api.opensea.io/api/v2/events/collection/{slug}"
    headers = {"x-api-key": OPENSEA_API_KEY}
    params = {
        "event_type": "mint",
        "after": int(since_timestamp),
        "limit": 20,
    }
    res = requests.get(url, headers=headers, params=params, timeout=10)
    res.raise_for_status()
    return res.json().get("asset_events", [])

async def send_mint_alert(col, token_id, short_addr, image_url, chain):
    """Send a mint alert with the NFT image if available."""
    slug = col.get("slug", "")
    contract = col.get("contract", "")

    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton(text="🌊 View on OpenSea", url=f"https://opensea.io/assets/{chain}/{contract}/{token_id}")]
    ])

    text = (
        f"🟢 <b>New Mint Detected!</b>\n\n"
        f"<b>{escape_html(col['name'])}</b> [{chain.capitalize()}]\n"
        f"Token ID: <b>#{token_id}</b>\n"
        f"Minted by: <code>{short_addr}</code>"
    )

    sent = False
    if image_url:
        try:
            image_bytes = await download_image_bytes(image_url)
            await asend_photo(image_bytes, caption=text, parse_mode="HTML", reply_markup=reply_markup)
            sent = True
        except Exception as e:
            print(f"[Mint] Photo send failed for #{token_id}: {e} — falling back to text")

    if not sent:
        await asend(text, reply_markup=reply_markup)

async def check_mints():
    print("[Mint] Checking for new mints...")

    # Merge static config collections with dynamic watchlist
    all_collections = merge_with_config(COLLECTIONS)

    for col in all_collections:
        contract = col["contract"]
        slug = col["slug"]
        chain = col.get("chain", "ethereum")
        try:
            since = last_seen.get(
                contract,
                (datetime.now(timezone.utc) - timedelta(minutes=2)).timestamp()
            )
            mints = await asyncio.to_thread(get_recent_mints, slug, since)

            if mints:
                latest_ts = max(m.get("event_timestamp", since) for m in mints)
                last_seen[contract] = latest_ts

                for mint in mints:
                    now = datetime.now(timezone.utc).timestamp()
                    last = mint_last_alerted.get(contract, 0)

                    if (now - last) < MINT_COOLDOWN_MINUTES * 60:
                        print(f"[Mint] Cooldown active for {col['name']}, skipping")
                        continue

                    mint_last_alerted[contract] = now

                    nft = mint.get("nft", {})
                    token_id = nft.get("identifier", "?")
                    to_addr = mint.get("to_address", "?")
                    short_addr = f"{to_addr[:6]}...{to_addr[-4:]}" if len(to_addr) > 10 else to_addr

                    # NFT image — OpenSea returns this inside the event payload
                    image_url = nft.get("image_url") or nft.get("display_image_url")

                    await send_mint_alert(col, token_id, short_addr, image_url, chain)
                    print(f"[Mint] {col['name']} [{chain}] Token #{token_id} minted by {short_addr}")
            else:
                print(f"[Mint] No new mints for {col['name']}")
                last_seen[contract] = datetime.now(timezone.utc).timestamp()

        except requests.exceptions.RequestException as e:
            print(f"[Mint Network Error] {col['name']}: {e}")
        except (KeyError, ValueError, TypeError) as e:
            print(f"[Mint Data Error] {col['name']}: {e}")
        except Exception as e:
            print(f"[Mint Unexpected Error] {col['name']}: {e}")
            raise
