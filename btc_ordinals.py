"""
btc_ordinals.py - Dedicated Bitcoin Ordinals & Inscriptions Scanner.

Scans the Bitcoin blockchain in real-time for fresh Ordinal inscriptions:
- Fetches new Inscriptions via public Ordinals indexing endpoints (ordinals.com / hiro.so)
- Extracts raw on-chain inscription content & images (ordinals.com/content/<id>)
- Passes inscription metadata through Gemini AI legitimacy audit
- Delivers instant Telegram photo alerts with Magic Eden BTC and Ordinals.com links
"""
import asyncio
import re
import time
from collections import deque
from datetime import datetime, timezone
import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from notifier import asend, asend_photo, download_image_bytes, escape_html
from gemini_filter import gemini_score_nft, is_worth_alerting, verdict_badge
import checkpoint

try:
    from private.config_live import GEMINI_MIN_SCORE
except ImportError:
    from config import GEMINI_MIN_SCORE

# Public Ordinals API endpoints with automatic failover
ORDINALS_APIS = [
    "https://api.hiro.so/ordinals/v1/inscriptions",
    "https://ordinals.com/inscriptions",
]

# Track alerted inscriptions. The in-memory pair is the fast path; checkpoint.py
# holds the durable copy so a restart does not re-alert recent inscriptions.
MAX_ALERTED_ORDINALS = 10000
SEEN_BTC = "btc_inscriptions"
alerted_ordinals_set = set()
alerted_ordinals_deque = deque(maxlen=MAX_ALERTED_ORDINALS)


def _parse_inscription_number(text: str) -> int:
    """Pull the inscription number out of detail-page text.

    ordinals.com renders it as "<h1>Inscription 127212232</h1>". Returns 0 when no
    number is present rather than guessing.
    """
    if not text:
        return 0
    match = re.search(r"Inscription\s+(\d[\d,]*)", text)
    if not match:
        match = re.search(r"(\d[\d,]*)", text)
    if not match:
        return 0
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return 0


def _fetch_inscription_details(iid: str, headers: dict) -> dict:
    """Read number and content type off an inscription's detail page.

    The listing page renders image tiles, so its anchors carry no text and no
    metadata: parsing them yielded "Ordinal #0" with an empty type for every
    alert. Both facts only exist at /inscription/<id>, so this costs one extra
    request per inscription.

    Returns empty values on any failure; a detail miss must not lose the
    inscription itself.
    """
    out = {"number": 0, "content_type": ""}
    try:
        res = requests.get(f"https://ordinals.com/inscription/{iid}",
                           headers=headers, timeout=10)
        if res.status_code != 200:
            return out
        html = res.text
    except Exception:
        return out

    heading = re.search(r"<h1>\s*Inscription\s+([\d,]+)\s*</h1>", html, re.I)
    if heading:
        out["number"] = _parse_inscription_number(heading.group(0))

    ctype = re.search(r"<dt>\s*content type\s*</dt>\s*<dd[^>]*>([^<]+)</dd>",
                      html, re.I)
    if ctype:
        out["content_type"] = ctype.group(1).strip()
    return out


def fetch_recent_inscriptions(limit: int = 15) -> list:
    """Fetch recent Bitcoin inscriptions directly from official Ordinals indexer.

    The listing page yields ids only. Number and content type come from each
    inscription's detail page, one request each, because inventing them produced
    "Ordinal #0" titles and an empty "Type:" line on every alert.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    url = "https://ordinals.com/inscriptions"
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(res.text, "html.parser")
            seen_ids = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if href.startswith("/inscription/"):
                    iid = href.split("/inscription/")[-1].strip("/")
                    if iid and iid not in seen_ids:
                        seen_ids.append(iid)
                    if len(seen_ids) >= limit:
                        break

            inscriptions = []
            for iid in seen_ids:
                details = _fetch_inscription_details(iid, headers)
                tx_id = iid.split("i")[0] if "i" in iid else iid
                inscriptions.append({
                    "id": iid,
                    "inscription_id": iid,
                    "number": details["number"],
                    "content_type": details["content_type"],
                    "address": "",
                    "tx_id": tx_id,
                    "image_url": f"https://ordinals.com/content/{iid}",
                })
            if inscriptions:
                return inscriptions
    except Exception as e:
        print(f"[Bitcoin] ⚠️ Error fetching from ordinals.com: {e}")

    return []


def parse_inscription_item(item: dict) -> dict:
    """Extract standard properties from raw inscription payload."""
    inscription_id = item.get("id") or item.get("inscription_id") or ""
    number = item.get("number", 0)
    content_type = item.get("content_type", "")
    address = item.get("address") or item.get("genesis_address") or item.get("creator") or ""
    tx_id = item.get("genesis_tx_id") or item.get("tx_id") or (inscription_id.split("i")[0] if "i" in inscription_id else "")
    timestamp = item.get("genesis_timestamp") or item.get("timestamp")

    # Image URL directly from on-chain sat content endpoint
    image_url = item.get("image_url") or ""
    if not image_url and (content_type.startswith("image/") or "svg" in content_type or content_type == "image/*"):
        image_url = f"https://ordinals.com/content/{inscription_id}"

    return {
        "inscription_id": inscription_id,
        "number": number,
        "content_type": content_type,
        "creator": address,
        "tx_id": tx_id,
        "timestamp": timestamp,
        "image_url": image_url,
    }


async def check_btc_ordinals():
    """Main scanning loop for Bitcoin Ordinals & Inscriptions."""
    try:
        inscriptions = await asyncio.to_thread(fetch_recent_inscriptions, 15)
        if not inscriptions:
            return

        for raw_item in inscriptions:
            item = parse_inscription_item(raw_item)
            inscription_id = item.get("inscription_id")
            if (not inscription_id or inscription_id in alerted_ordinals_set
                    or checkpoint.was_seen(SEEN_BTC, inscription_id)):
                continue

            number = item.get("number", 0)
            content_type = item.get("content_type", "unknown")
            creator = item.get("creator", "")
            image_url = item.get("image_url")
            short_id = f"{inscription_id[:6]}...{inscription_id[-6:]}"

            # ── Gemini AI Audit ───────────────────────────────────────────────
            ai_result = await gemini_score_nft({
                "contract":                 inscription_id,
                "chain":                    "bitcoin",
                "name":                     f"Bitcoin Ordinal #{number}",
                "symbol":                   "BTC",
                "mint_count":               1,
                "age_hours":                0.1,
                "standard":                 f"Ordinal ({content_type})",
                "unique_minters":           1,
                "mint_velocity_per_hour":   5.0,
                "token_uri":                image_url,
                "metadata":                 item,
                "verified_source_snippet": None,
                "deployer_address":         creator,
                "deployer_stats":           None,
                "ethos_profile":            None,
                "dex_liquidity":            {},
            })

            if not is_worth_alerting(ai_result, GEMINI_MIN_SCORE):
                continue

            # ── Dedup ─────────────────────────────────────────────────────────
            # Recorded BEFORE the send so a crash mid-send cannot re-alert this
            # inscription after a restart.
            if len(alerted_ordinals_deque) == MAX_ALERTED_ORDINALS:
                oldest = alerted_ordinals_deque.popleft()
                alerted_ordinals_set.discard(oldest)
            alerted_ordinals_set.add(inscription_id)
            alerted_ordinals_deque.append(inscription_id)
            checkpoint.mark_seen(SEEN_BTC, inscription_id)

            # ── Build Telegram Buttons ────────────────────────────────────────
            ordinals_url = f"https://ordinals.com/inscription/{inscription_id}"
            magiceden_btc_url = f"https://magiceden.io/ordinals/item-details/{inscription_id}"
            mempool_url = f"https://mempool.space/tx/{item.get('tx_id', '')}" if item.get("tx_id") else ordinals_url

            button_rows = [
                [
                    InlineKeyboardButton(text="🟧 Ordinals.com", url=ordinals_url),
                    InlineKeyboardButton(text="🪄 Magic Eden BTC", url=magiceden_btc_url),
                ],
                [
                    InlineKeyboardButton(text="⛓️ Mempool TX", url=mempool_url),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(button_rows)

            # ── Build Telegram Message ────────────────────────────────────────
            creator_line = f"\n👤 Inscriber: <code>{creator[:6]}...{creator[-4:]}</code>" if creator else ""
            text = (
                f"🆕 <b>New Bitcoin Ordinal Inscribed!</b>\n\n"
                f"<b>Ordinal #{number}</b>\n"
                f"🔗 Chain: <b>Bitcoin (Ordinals)</b>\n"
                f"📄 Inscription: <code>{short_id}</code>{creator_line}\n"
                f"🏷️ Type: <code>{content_type}</code>\n\n"
                f"<b>AI Legitimacy Audit:</b>\n"
                f"{verdict_badge(ai_result)}"
            )

            # ── Send Photo / Text Alert ───────────────────────────────────────
            sent = False
            if image_url:
                try:
                    img_bytes = await download_image_bytes(image_url)
                    if img_bytes:
                        await asend_photo(img_bytes, caption=text, parse_mode="HTML", reply_markup=reply_markup)
                        sent = True
                except Exception as photo_err:
                    print(f"[Bitcoin] Photo send failed: {photo_err} — falling back to text")

            if not sent:
                await asend(text, reply_markup=reply_markup)

            print(f"[Bitcoin] 🆕 Alerted: Ordinal #{number} ({short_id}) | AI {ai_result['score']}/100")

    except Exception as e:
        print(f"[Bitcoin Error] Ordinals: {e}")

    # Persist the inscription dedup history so a restart does not re-alert the
    # inscriptions still sitting on the API's recent page.
    try:
        checkpoint.flush(force=True)
    except Exception as e:
        print(f"[Bitcoin] ⚠️ Checkpoint flush failed: {e}")
