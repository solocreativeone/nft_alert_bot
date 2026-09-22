import os
import requests
import asyncio
from datetime import datetime, timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from watchlist import merge_with_config, get_watchlist, save_watchlist, get_collection_url, normalize_contract
from notifier import asend, asend_photo, download_image_bytes, escape_html
from alert_history import record_alert
import checkpoint

try:
    from private.config_live import (
        OPENSEA_API_KEY,
        COLLECTIONS,
        FLOOR_COOLDOWN_MINUTES,
        WATCH_FLOOR_CHANGE_PERCENT,
    )
except ImportError:
    try:
        from config import (
            OPENSEA_API_KEY,
            COLLECTIONS,
            FLOOR_COOLDOWN_MINUTES,
            WATCH_FLOOR_CHANGE_PERCENT,
        )
    except ImportError:
        from config import OPENSEA_API_KEY, COLLECTIONS, FLOOR_COOLDOWN_MINUTES
        WATCH_FLOOR_CHANGE_PERCENT = 10.0

# Cooldown tracker
floor_last_alerted = {}


def get_floor_change_threshold() -> float:
    """Get floor change percentage threshold from env, falling back to config."""
    env_val = os.environ.get("WATCH_FLOOR_CHANGE_PERCENT")
    if env_val is not None:
        try:
            return float(env_val)
        except ValueError:
            pass
    return float(WATCH_FLOOR_CHANGE_PERCENT)


def get_floor_and_image(slug):
    if not slug:
        return None, None

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


async def send_floor_signal(col, prev_floor, current_floor, direction, image_url=None, eth_usd_price=None):
    """Send a Floor Pump or Floor Dump signal for a watched collection."""
    if direction in ("pump", "high", "up"):
        headline = "🚀 <b>Floor Pump</b>"
    else:
        headline = "🔻 <b>Floor Dump</b>"

    chain = col.get("chain", "ethereum").capitalize()
    name = col.get("name", "Unknown")
    collection_url = get_collection_url(col)

    change_pct = ((current_floor - prev_floor) / prev_floor) * 100.0

    from price_utils import get_eth_usd_price, format_eth, format_usd
    eth_usd = eth_usd_price if eth_usd_price is not None else get_eth_usd_price()
    usd_str = format_usd(current_floor * eth_usd) if (eth_usd is not None and eth_usd > 0) else "N/A"

    reply_markup = None
    if collection_url:
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(text="🌊 View on OpenSea", url=collection_url)]
        ])

    text = (
        f"{headline}\n\n"
        f"<b>{escape_html(name)}</b> [{chain}]\n\n"
        f"Floor: {format_eth(prev_floor)} ETH → {format_eth(current_floor)} ETH\n"
        f"Change: {change_pct:+.2f}%\n"
        f"Current: {usd_str}"
    )

    sent = False
    if image_url:
        try:
            image_bytes = await download_image_bytes(image_url)
            if image_bytes:
                await asend_photo(image_bytes, caption=text, parse_mode="HTML", reply_markup=reply_markup)
                sent = True
        except Exception as e:
            print(f"[Floor] Photo send failed: {e} — falling back to text")

    if not sent:
        await asend(text, reply_markup=reply_markup)

    record_alert(
        "floor_signal", col, prev_floor, current_floor, change_pct,
        context={"direction": direction},
    )


async def check_watched_floor_signals():
    """Poll floor prices for all watched collections and trigger floor movement signals."""
    watchlist = get_watchlist()
    if not watchlist:
        return

    threshold = get_floor_change_threshold()
    dirty = False

    for item in watchlist:
        contract = normalize_contract(item.get("contract"))
        chain = item.get("chain", "ethereum").lower()
        key = f"{chain}:{contract}"
        slug = item.get("slug", "")

        try:
            floor, image_url = await asyncio.to_thread(get_floor_and_image, slug)
        except Exception as e:
            print(f"[Floor] Error fetching floor for {item.get('name')}: {e}")
            continue

        # Handle floor price = 0 / missing floor safely
        if floor is None or floor <= 0:
            continue

        # The chain-qualified checkpoint is the sole signal baseline.
        # last_floor is kept in the watchlist as a synchronized display/state
        # field, but is never allowed to override this canonical value.
        prev_floor = checkpoint.get_floor(key)

        # First floor observation: establish baseline without sending pump/dump signal
        if prev_floor is None or prev_floor <= 0:
            checkpoint.set_floor(key, floor, flush_now=True)
            item["last_floor"] = floor
            item["current_floor"] = floor
            dirty = True
            continue

        # The checkpoint/last_floor baseline is the sole source for signal
        # comparisons. current_floor is display state and must not suppress a
        # qualifying move that occurred while its alert cooldown was active.
        if floor == prev_floor:
            continue

        # Calculate percentage movement from baseline
        change_pct = ((floor - prev_floor) / prev_floor) * 100.0

        if change_pct >= threshold:
            direction = "pump"
        elif change_pct <= -threshold:
            direction = "dump"
        else:
            # Movement below threshold sends nothing
            item["current_floor"] = floor
            dirty = True
            continue

        # Respect the existing alert cooldown without changing the persisted
        # baseline. A later poll can still report the meaningful movement.
        now = datetime.now(timezone.utc).timestamp()
        last_alert = floor_last_alerted.get(key, 0)
        if (now - last_alert) < FLOOR_COOLDOWN_MINUTES * 60:
            item["current_floor"] = floor
            dirty = True
            continue

        # Trigger floor signal
        await send_floor_signal(item, prev_floor, floor, direction, image_url)
        floor_last_alerted[key] = now

        # Persist the new baseline floor to survive restart/reload
        checkpoint.set_floor(key, floor, flush_now=True)
        item["last_floor"] = floor
        item["current_floor"] = floor
        dirty = True

    if dirty:
        save_watchlist(watchlist)


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

    from price_utils import get_eth_usd_price, format_floor_display
    eth_usd = get_eth_usd_price()
    floor_str = format_floor_display(floor, eth_usd, False, is_alert=True) or f"💰 Floor: {floor} ETH"

    text = (
        f"{headline}\n\n"
        f"<b>{escape_html(col['name'])}</b> [{chain}]\n"
        f"{floor_str}\n"
        f"{direction_line}"
    )

    disable_notification = (direction == "low" and bool(col.get("silent_floor_drop_alert", False)))

    sent = False
    if image_url:
        try:
            image_bytes = await download_image_bytes(image_url)
            if image_bytes:
                await asend_photo(
                    image_bytes,
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                    disable_notification=disable_notification,
                )
                sent = True
        except Exception as e:
            print(f"[Floor] Photo send failed: {e} — falling back to text")

    if not sent:
        await asend(text, reply_markup=reply_markup, disable_notification=disable_notification)

    record_alert(
        "floor_target", col, current_floor=floor,
        context={"direction": direction, "threshold": col.get("floor_alert_low") if direction == "low" else col.get("floor_alert_high")},
    )


async def check_floors():
    print("[Floor] Running floor price check...")

    # First, process floor signals for dynamically watched collections
    await check_watched_floor_signals()

    # Next, check static target-based alerts for collections in config
    watched_contracts = {w.get("contract", "").lower() for w in get_watchlist() if "contract" in w}
    all_collections = merge_with_config(COLLECTIONS)

    for col in all_collections:
        if col.get("contract", "").lower() in watched_contracts:
            # Watched collections generate floor signals instead of static target alerts
            continue
        try:
            floor, image_url = await asyncio.to_thread(get_floor_and_image, col.get("slug", ""))
            if floor is None:
                continue  # Rate limited — skip this cycle

            print(f"[Floor] {col['name']}: {floor} ETH")

            now = datetime.now(timezone.utc).timestamp()
            last = floor_last_alerted.get(col["slug"], 0)

            if (now - last) < FLOOR_COOLDOWN_MINUTES * 60:
                print(f"[Floor] Cooldown active for {col['name']}, skipping alert")
                continue

            if col.get("floor_alerts_enabled", True):
                if (
                    col.get("floor_drop_alert_enabled", True)
                    and floor < col["floor_alert_low"]
                    ):
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
