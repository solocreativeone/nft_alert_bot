"""Shared Telegram helper — one Bot instance and common send utilities.

The Bot is created lazily on first use (not at import) so modules can be
imported without a TELEGRAM_TOKEN present — this is what makes them testable.
"""
import asyncio
import io

from telegram import Bot
from curl_cffi import requests

# Private config takes priority; fall back to public config
try:
    from private.config_live import TELEGRAM_TOKEN, CHAT_ID
except ImportError:
    from config import TELEGRAM_TOKEN, CHAT_ID

_bot = None


def get_bot():
    """Return the shared Bot, creating it on first use."""
    global _bot
    if _bot is None:
        _bot = Bot(token=TELEGRAM_TOKEN)
    return _bot


def escape_html(text):
    """Escape HTML special characters for safe Telegram parsing."""
    if not text:
        return ""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def download_image_bytes(url):
    """Download an image with IPFS gateway fallback, URL validation, and data URI support."""
    if not url:
        return None

    loop = asyncio.get_running_loop()

    # Handle data: URI images (base64-encoded SVGs, PNGs, etc.)
    if url.startswith("data:image"):
        try:
            raw_data = url.split(",", 1)[1]
            import base64
            return io.BytesIO(base64.b64decode(raw_data))
        except Exception:
            return None

    # Build list of URLs to try (IPFS gateway fallback)
    IPFS_GATEWAYS = [
        "https://ipfs.io/ipfs/",
        "https://dweb.link/ipfs/",
        "https://cloudflare-ipfs.com/ipfs/",
        "https://gateway.pinata.cloud/ipfs/",
    ]

    urls_to_try = []
    # Extract IPFS CID and build gateway list
    ipfs_cid = None
    for gw in IPFS_GATEWAYS:
        if gw in url:
            ipfs_cid = url.split("/ipfs/")[-1]
            break
    if ipfs_cid:
        urls_to_try = [f"{gw}{ipfs_cid}" for gw in IPFS_GATEWAYS]
    else:
        urls_to_try = [url]

    def _fetch(target_url):
        # Basic URL validation to prevent curl crashes
        if not target_url.startswith(("http://", "https://")):
            raise ValueError(f"Invalid URL scheme: {target_url[:50]}")
        res = requests.get(
            target_url,
            impersonate="chrome110",
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
            },
            timeout=10,
        )
        res.raise_for_status()
        return io.BytesIO(res.content)

    for try_url in urls_to_try:
        try:
            return await loop.run_in_executor(None, _fetch, try_url)
        except Exception:
            continue

    raise RuntimeError(f"All image download attempts failed for: {url[:80]}")


async def asend(text, parse_mode="HTML", reply_markup=None):
    """Async send — HTML mode by default, link previews suppressed."""
    await get_bot().send_message(
        chat_id=CHAT_ID,
        text=text,
        parse_mode=parse_mode,
        disable_web_page_preview=True,
        reply_markup=reply_markup,
    )


async def asend_photo(photo, caption=None, parse_mode=None, reply_markup=None):
    """Async photo send — use inside an existing event loop."""
    # Telegram hard limit for photo captions is 1024 chars
    if caption and len(caption) > 1024:
        caption = caption[:1020] + "..."
    await get_bot().send_photo(
        chat_id=CHAT_ID,
        photo=photo,
        caption=caption,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
    )

