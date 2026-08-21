"""
metadata_resolver.py - Decentralized On-Chain NFT Metadata & Image Resolver.

Extracts and resolves tokenURI directly from on-chain data:
- IPFS gateways (ipfs:// -> ipfs.io, dweb.link, cloudflare-ipfs.com)
- Arweave gateways (ar:// -> arweave.net)
- Data URIs (data:application/json;base64, data:image/svg+xml)
- Standard HTTPS endpoints
Uses curl_cffi with browser impersonation to bypass Cloudflare protection.
"""
import asyncio
import base64
import json
import re
from curl_cffi import requests

IPFS_GATEWAYS = [
    "https://ipfs.io/ipfs/",
    "https://dweb.link/ipfs/",
    "https://gateway.pinata.cloud/ipfs/",
    "https://w3s.link/ipfs/",
]

ARWEAVE_GATEWAY = "https://arweave.net/"


def resolve_uri(uri: str) -> str:
    """Normalize IPFS, Arweave, and custom URI schemes to reachable HTTPS URLs."""
    if not uri:
        return ""

    uri = uri.strip()

    # Skip data URIs — they're handled separately
    if uri.startswith("data:"):
        return uri

    # Handle IPFS
    if uri.startswith("ipfs://"):
        cid_path = uri[7:].lstrip("/")
        return f"{IPFS_GATEWAYS[0]}{cid_path}"
    elif "ipfs/" in uri and not uri.startswith("http"):
        cid_path = uri.split("ipfs/")[-1].lstrip("/")
        return f"{IPFS_GATEWAYS[0]}{cid_path}"

    # Handle Arweave
    if uri.startswith("ar://"):
        tx_id = uri[5:].lstrip("/")
        return f"{ARWEAVE_GATEWAY}{tx_id}"

    # Sanitize: reject non-HTTP(S) URLs that made it this far
    if uri.startswith("http://") or uri.startswith("https://"):
        return uri

    # Unknown scheme — skip it to prevent curl crashes
    return ""


def parse_data_uri_json(uri: str) -> dict:
    """Decode on-chain Base64 or URL-encoded JSON data URIs."""
    try:
        if uri.startswith("data:application/json;base64,"):
            raw_b64 = uri.split("data:application/json;base64,")[1]
            decoded = base64.b64decode(raw_b64).decode("utf-8", errors="ignore")
            return json.loads(decoded)
        elif uri.startswith("data:application/json,") or uri.startswith("data:application/json;utf8,"):
            raw_json = uri.split(",", 1)[1]
            return json.loads(raw_json)
    except Exception:
        pass
    return {}


def fetch_metadata_sync(token_uri: str) -> dict:
    """Synchronously fetch and parse metadata JSON from a tokenURI."""
    if not token_uri:
        return {}

    token_uri = token_uri.strip()

    # Check for direct data URI
    if token_uri.startswith("data:application/json"):
        data = parse_data_uri_json(token_uri)
        if data:
            return _normalize_metadata(data)

    resolved_url = resolve_uri(token_uri)
    if not resolved_url or not resolved_url.startswith("http"):
        return {}

    # If it's an IPFS URL, try each gateway if one fails
    urls_to_try = []
    if "ipfs.io/ipfs/" in resolved_url:
        cid = resolved_url.split("ipfs.io/ipfs/")[-1]
        urls_to_try = [f"{gw}{cid}" for gw in IPFS_GATEWAYS]
    else:
        urls_to_try = [resolved_url]

    for url in urls_to_try:
        try:
            res = requests.get(
                url,
                impersonate="chrome110",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
                    "Accept": "application/json, text/plain, */*",
                },
                timeout=8,
            )
            if res.status_code == 200:
                try:
                    data = res.json()
                    if isinstance(data, dict):
                        return _normalize_metadata(data)
                except Exception:
                    # Might be raw image directly in tokenURI
                    content_type = res.headers.get("content-type", "").lower()
                    if any(t in content_type for t in ["image/", "svg", "png", "jpeg", "webp"]):
                        return {"image_url": url}
        except Exception:
            continue

    return {}


_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".avif")


def _looks_like_image(url: str) -> bool:
    """True if a URL/URI is very likely a direct image (not video/HTML/3D model)."""
    if not url:
        return False
    u = url.strip().lower()
    if u.startswith("data:image"):
        return True
    path = u.split("?", 1)[0].split("#", 1)[0]
    return path.endswith(_IMAGE_EXTENSIONS)


def _normalize_metadata(raw_data: dict) -> dict:
    """Extract standard attributes from raw metadata JSON."""
    name = raw_data.get("name") or ""
    description = raw_data.get("description") or ""

    # Prefer real image fields.
    image_raw = (
        raw_data.get("image")
        or raw_data.get("image_url")
        or raw_data.get("artwork_url")
        or ""
    )

    # image_data usually holds raw on-chain SVG markup (or a full data URI).
    image_data = raw_data.get("image_data") or ""

    # Only fall back to animation_url when it actually points at an image — it's
    # frequently an .mp4/.glb/.html which Telegram can't render as a photo.
    if not image_raw and not image_data:
        animation = raw_data.get("animation_url") or ""
        if _looks_like_image(animation):
            image_raw = animation

    image_url = ""
    if image_raw:
        image_url = resolve_uri(image_raw)
    elif image_data:
        stripped = image_data.strip()
        if stripped.startswith("data:"):
            image_url = stripped
        elif "<svg" in stripped[:256].lower():
            # Raw SVG markup embedded directly in metadata — expose it as a data
            # URI so the notifier can rasterize it to PNG for Telegram.
            from urllib.parse import quote
            image_url = "data:image/svg+xml;utf8," + quote(stripped)

    return {
        "name": name,
        "description": description,
        "image_url": image_url,
        "attributes": raw_data.get("attributes", []),
        "raw": raw_data,
    }


async def resolve_metadata_async(token_uri: str) -> dict:
    """Asynchronously fetch metadata and extract image URL."""
    return await asyncio.to_thread(fetch_metadata_sync, token_uri)
