"""Shared Telegram helper — one Bot instance and common send utilities.

The Bot is created lazily on first use (not at import) so modules can be
imported without a TELEGRAM_TOKEN present — this is what makes them testable.
"""
import asyncio
import base64
import io
from typing import Optional
from urllib.parse import unquote

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


# IPFS gateways for fetching images (cloudflare-ipfs.com is dead — removed).
IPFS_GATEWAYS = [
    "https://ipfs.io/ipfs/",
    "https://dweb.link/ipfs/",
    "https://gateway.pinata.cloud/ipfs/",
    "https://w3s.link/ipfs/",
]

# Telegram rejects photo uploads larger than ~10 MB — stay just under.
MAX_IMAGE_BYTES = int(9.5 * 1024 * 1024)


def _looks_like_svg(data: bytes) -> bool:
    """Sniff whether raw bytes are an SVG document."""
    if not data:
        return False
    head = data[:512].lstrip()
    if head[:4].lower() == b"<svg":
        return True
    return head[:5].lower() == b"<?xml" and b"<svg" in head.lower()


def _rasterize_svg(svg_bytes):
    """Convert SVG bytes to PNG bytes. None on failure.

    Telegram's sendPhoto does not accept SVG, so fully-on-chain SVG art must be
    rasterized before sending.

    Two backends are tried in order:
      1. cairosvg: ships as a pure wheel (cairocffi loads libcairo at runtime),
         so it works on a plain `pip install -r requirements.txt`.
      2. svglib + reportlab renderPM: fallback. NOTE: reportlab 4.x/5.x wheels
         are pure-Python and no longer bundle the _renderPM C extension, so this
         path raises "cannot import desired renderPM backend rlPyCairo" unless
         rlPyCairo/pycairo is separately compiled. Kept as a secondary in case
         it is available.
    """
    try:
        import cairosvg

        png = cairosvg.svg2png(bytestring=svg_bytes)
        if png:
            return png
    except Exception as e:
        print(f"[Image] cairosvg rasterize failed ({e}); trying reportlab")

    try:
        from svglib.svglib import svg2rlg
        from reportlab.graphics import renderPM

        drawing = svg2rlg(io.BytesIO(svg_bytes))
        if drawing is None:
            return None
        return renderPM.drawToString(drawing, fmt="PNG")
    except Exception as e:
        print(f"[Image] SVG rasterize failed: {e}")
        return None


def _sniff_image_kind(data: bytes) -> Optional[str]:
    """Return an extension for recognised bitmap magic bytes, else None.

    Magic bytes are the only trustworthy signal. Declared content types are
    routinely wrong: the ordinals.com scraper reports "image/*" for every
    inscription, and IPFS gateways return HTML error pages with image types.
    """
    if not data:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _guess_extension(content_type: str, data: bytes) -> str:
    """Pick a filename extension so Telegram can detect the image format.

    Magic bytes win over the declared type. Falls back to "png" only for callers
    that have already established the payload is an image.
    """
    sniffed = _sniff_image_kind(data)
    if sniffed:
        return sniffed
    ct = (content_type or "").lower()
    if "png" in ct:
        return "png"
    if "jpeg" in ct or "jpg" in ct:
        return "jpg"
    if "gif" in ct:
        return "gif"
    if "webp" in ct:
        return "webp"
    return "png"


def _finalize_image(data: bytes, content_type: str = ""):
    """Turn raw image bytes into a Telegram-ready BytesIO (SVG->PNG, correct
    filename set), or None if it can't be turned into a sendable photo.

    Rejects anything whose bytes are not a supported bitmap. Sending unverified
    bytes is what produced Telegram's Image_process_failed on 13 of 13 Ordinal
    alerts: inscriptions are frequently text, HTML, or video, and a declared
    content type of "image/*" carries no subtype to catch that.
    """
    if not data:
        return None
    ct = (content_type or "").lower()
    # SVG isn't accepted by Telegram sendPhoto — rasterize to PNG.
    if "svg" in ct or _looks_like_svg(data):
        png = _rasterize_svg(data)
        if not png:
            return None
        data = png
        ct = "image/png"
    kind = _sniff_image_kind(data)
    if not kind:
        preview = data[:16]
        print(f"[Image] Not a supported image; refusing to send as photo "
              f"(declared: {content_type or 'none'}, {len(data)} bytes, starts {preview!r})")
        return None
    if len(data) > MAX_IMAGE_BYTES:
        print(f"[Image] Skipping oversized image ({len(data)} bytes > {MAX_IMAGE_BYTES})")
        return None
    bio = io.BytesIO(data)
    bio.name = f"image.{kind}"
    bio.seek(0)
    return bio


async def download_image_bytes(url):
    """Fetch an NFT image and return a Telegram-ready BytesIO (SVG rasterized to
    PNG, correct filename set), or None if it can't be turned into a photo.

    Callers should guard with ``if img_bytes:`` and fall back to a text alert.
    """
    if not url:
        return None

    # ── data: URIs (on-chain SVG/PNG, base64 OR URL-encoded) ──────────
    if url.startswith("data:"):
        try:
            header, _, payload = url.partition(",")
            mime = header[5:].split(";")[0].strip()  # between "data:" and first ";"/","
            if ";base64" in header.lower():
                raw = base64.b64decode(payload)
            else:
                raw = unquote(payload).encode("utf-8", errors="ignore")
            ct = mime or ("image/svg+xml" if _looks_like_svg(raw) else "")
            return _finalize_image(raw, content_type=ct)
        except Exception as e:
            print(f"[Image] data URI decode failed: {e}")
            return None

    # ── Build candidate URLs (IPFS gateway fallback) ──────────────────
    if "/ipfs/" in url:
        cid = url.split("/ipfs/")[-1]
        urls_to_try = [f"{gw}{cid}" for gw in IPFS_GATEWAYS]
    else:
        urls_to_try = [url]

    def _fetch(target_url):
        # Basic URL validation to prevent curl crashes.
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
        return res.content, res.headers.get("content-type", "")

    loop = asyncio.get_running_loop()
    for try_url in urls_to_try:
        try:
            content, content_type = await loop.run_in_executor(None, _fetch, try_url)
            ct = (content_type or "").lower()
            # Skip gateway error pages (HTML/JSON) that aren't the image itself.
            if ("text/html" in ct or "application/json" in ct) and not _looks_like_svg(content):
                continue
            img = _finalize_image(content, content_type=content_type)
            if img:
                return img
        except Exception:
            continue

    print(f"[Image] All download attempts failed for: {url[:80]}")
    return None


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

