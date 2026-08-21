"""
solana_drops.py - Dedicated Raw JSON-RPC Scanner for Solana NFT Drops.

Scans the Solana blockchain directly using standard Solana JSON-RPC endpoints:
- Metaplex Candy Machine v3 (cndy3Z4yapfJBmL3DGmm5pkydaxDoZSfhHJtuW6WzB)
- Metaplex Core (CoREENxT6tW1HoK8ypY1SxRMZTcVPm7R94rH4PZNhX7d)
- Metaplex Token Metadata (metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s)

Extracts Arweave/IPFS metadata on-chain, passes context through Gemini AI audit,
and delivers instant photo alerts to Telegram.
"""
import asyncio
import time
from collections import deque
from datetime import datetime, timezone
import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from notifier import asend, asend_photo, download_image_bytes, escape_html
from gemini_filter import gemini_score_nft, is_worth_alerting, verdict_badge
from metadata_resolver import resolve_metadata_async

try:
    from private.config_live import GEMINI_MIN_SCORE
except ImportError:
    from config import GEMINI_MIN_SCORE

# Public Solana RPC endpoints with automatic failover
SOLANA_RPCS = [
    "https://api.mainnet-beta.solana.com",
    "https://solana-rpc.publicnode.com",
    "https://rpc.ankr.com/solana",
]

# Metaplex Program IDs
METAPLEX_CORE_PROGRAM          = "CoREENxT6tW1HoK8ypY1SxRMZTcVPm7R94rH4PZNhX7d"
CANDY_MACHINE_CORE_PROGRAM     = "CndyV3LdqHUfDLmE5naZjVN8rBZz4tqhdefbAnjHG3JR"
METAPLEX_TOKEN_METADATA_PROGRAM = "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s"

PROGRAMS_TO_WATCH = [
    ("Metaplex Core", METAPLEX_CORE_PROGRAM),
    ("Candy Machine Core", CANDY_MACHINE_CORE_PROGRAM),
    ("Token Metadata", METAPLEX_TOKEN_METADATA_PROGRAM),
]

# Known non-NFT token mints to ignore (USDC, USDT, WSOL, etc.)
NON_NFT_MINTS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", # USDT
    "So11111111111111111111111111111111111111112", # Wrapped SOL
}

# Track alerted mints/collections to prevent duplicate notifications
MAX_ALERTED_SOLANA = 10000
alerted_solana_set = set()
alerted_solana_deque = deque(maxlen=MAX_ALERTED_SOLANA)

# Last processed signature per program
last_signatures = {}


def solana_rpc_post(payload: dict):
    """Direct Solana JSON-RPC dispatcher with automatic retry and node failover."""
    for rpc_url in SOLANA_RPCS:
        for attempt in range(2):
            try:
                res = requests.post(rpc_url, json=payload, timeout=8)
                if res.status_code == 429:
                    time.sleep(0.3 * (attempt + 1))
                    continue
                if res.status_code == 200:
                    data = res.json()
                    if "error" in data:
                        break
                    return data
            except Exception:
                continue
    return None


async def get_recent_signatures(program_id: str, limit: int = 10):
    """Fetch recent confirmed transaction signatures for a program."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignaturesForAddress",
        "params": [
            program_id,
            {"limit": limit}
        ]
    }
    data = await asyncio.to_thread(solana_rpc_post, payload)
    if not data or "result" not in data:
        return []
    return data["result"]


async def get_parsed_transaction(signature: str):
    """Fetch and parse transaction details to extract mint and metadata."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [
            signature,
            {
                "encoding": "jsonParsed",
                "maxSupportedTransactionVersion": 0
            }
        ]
    }
    data = await asyncio.to_thread(solana_rpc_post, payload)
    if not data or not data.get("result"):
        return None
    return data["result"]


def extract_solana_mint_info(tx_data: dict) -> dict:
    """Extract creator, NFT mint account (decimals=0), and metadata URI."""
    if not tx_data:
        return {}

    meta = tx_data.get("meta", {})
    if meta.get("err") is not None:
        return {}  # Skip failed transactions

    transaction = tx_data.get("transaction", {})
    message = transaction.get("message", {})
    account_keys = message.get("accountKeys", [])

    fee_payer = ""
    for acc in account_keys:
        if isinstance(acc, dict):
            pubkey = acc.get("pubkey", "")
            if acc.get("signer") and not fee_payer:
                fee_payer = pubkey
        elif isinstance(acc, str) and not fee_payer:
            fee_payer = acc

    # Search log messages for URIs
    log_messages = meta.get("logMessages", [])
    token_uri = ""
    for log in log_messages:
        if "arweave.net/" in log or "ipfs" in log or "http" in log:
            for part in log.split():
                if part.startswith("http://") or part.startswith("https://") or part.startswith("ar://") or part.startswith("ipfs://"):
                    token_uri = part.rstrip(",;\"'")
                    break

    # Look for newly created NFT mint in postTokenBalances (decimals MUST be 0 for standard NFT)
    mint_address = ""
    post_token_balances = meta.get("postTokenBalances", [])
    for balance in post_token_balances:
        mint = balance.get("mint", "")
        if mint in NON_NFT_MINTS:
            continue
        ui_token_amount = balance.get("uiTokenAmount", {})
        decimals = ui_token_amount.get("decimals", 0)
        # Standard NFT has 0 decimals
        if decimals == 0:
            mint_address = mint
            break

    if not mint_address and post_token_balances:
        # Fallback to first non-standard token if not in exclusion list
        first_mint = post_token_balances[0].get("mint", "")
        if first_mint not in NON_NFT_MINTS:
            mint_address = first_mint

    # Fallback for Metaplex Core (Asset account is created in instructions)
    if not mint_address and account_keys:
        for acc in account_keys:
            pk = acc.get("pubkey") if isinstance(acc, dict) else acc
            if pk and pk != fee_payer and pk not in NON_NFT_MINTS and not pk.startswith("11111") and not pk.startswith("ComputeBudget") and not pk.startswith("CoREENxT"):
                mint_address = pk
                break

    return {
        "mint_address": mint_address,
        "creator": fee_payer,
        "token_uri": token_uri,
    }


async def check_solana_drops():
    """Main Solana mint scanner loop across Candy Machine v3 and Metaplex Core."""
    global last_signatures

    for program_name, program_id in PROGRAMS_TO_WATCH:
        try:
            sigs = await get_recent_signatures(program_id, limit=8)
            if not sigs:
                continue

            last_sig = last_signatures.get(program_id)
            last_signatures[program_id] = sigs[0].get("signature")

            # First run: seed watermark and continue
            if last_sig is None:
                continue

            for sig_info in sigs:
                sig = sig_info.get("signature")
                if sig == last_sig:
                    break  # Reached previously processed batch

                if sig in alerted_solana_set:
                    continue

                tx_data = await get_parsed_transaction(sig)
                if not tx_data:
                    continue

                mint_info = extract_solana_mint_info(tx_data)
                mint_address = mint_info.get("mint_address")
                creator = mint_info.get("creator")
                token_uri = mint_info.get("token_uri")

                if not mint_address or mint_address in alerted_solana_set or mint_address in NON_NFT_MINTS:
                    continue

                short_mint = f"{mint_address[:6]}...{mint_address[-4:]}"

                # ── Fetch Metadata & Artwork from Arweave / IPFS ─────────────
                metadata = await resolve_metadata_async(token_uri) if token_uri else {}
                name = metadata.get("name")
                image_url = metadata.get("image_url")

                # If no name or image could be resolved, skip low-quality noise
                if not name and not image_url and not token_uri:
                    continue

                display_name = name or f"Solana Drop {short_mint}"

                # ── Gemini AI Audit ───────────────────────────────────────────
                ai_result = await gemini_score_nft({
                    "contract":                 mint_address,
                    "chain":                    "solana",
                    "name":                     display_name,
                    "symbol":                   "SOL",
                    "mint_count":               1,
                    "age_hours":                0.1,
                    "standard":                 program_name,
                    "unique_minters":           1,
                    "mint_velocity_per_hour":   10.0,
                    "token_uri":                token_uri,
                    "metadata":                 metadata,
                    "verified_source_snippet": None,
                    "deployer_address":         creator,
                    "deployer_stats":           None,
                    "ethos_profile":            None,
                    "dex_liquidity":            {},
                })

                if not is_worth_alerting(ai_result, GEMINI_MIN_SCORE):
                    continue

                # ── Dedup ─────────────────────────────────────────────────────
                if len(alerted_solana_deque) == MAX_ALERTED_SOLANA:
                    oldest = alerted_solana_deque.popleft()
                    alerted_solana_set.discard(oldest)
                alerted_solana_set.add(mint_address)
                alerted_solana_deque.append(mint_address)
                alerted_solana_set.add(sig)

                # ── Build Telegram Buttons ────────────────────────────────────
                solscan_link = f"https://solscan.io/token/{mint_address}"
                magiceden_link = f"https://magiceden.io/item-details/{mint_address}"
                tensor_link = f"https://www.tensor.trade/item/{mint_address}"

                button_rows = [
                    [
                        InlineKeyboardButton(text="🔍 Solscan", url=solscan_link),
                        InlineKeyboardButton(text="⚡ Tensor", url=tensor_link),
                    ],
                    [
                        InlineKeyboardButton(text="🪄 Magic Eden", url=magiceden_link),
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(button_rows)

                # ── Build Telegram Message ────────────────────────────────────
                creator_line = f"\n👤 Creator: <code>{creator[:6]}...{creator[-4:]}</code>" if creator else ""
                text = (
                    f"🆕 <b>New Solana NFT Drop Detected!</b>\n\n"
                    f"<b>{escape_html(display_name)}</b>\n"
                    f"🔗 Chain: <b>Solana</b>\n"
                    f"📄 Mint: <code>{short_mint}</code>{creator_line}\n"
                    f"🏷️ Standard: <b>{program_name}</b>\n\n"
                    f"<b>AI Legitimacy Audit:</b>\n"
                    f"{verdict_badge(ai_result)}"
                )

                # ── Send Photo / Text Alert ───────────────────────────────────
                sent = False
                if image_url:
                    try:
                        img_bytes = await download_image_bytes(image_url)
                        if img_bytes:
                            await asend_photo(img_bytes, caption=text, parse_mode="HTML", reply_markup=reply_markup)
                            sent = True
                    except Exception as photo_err:
                        print(f"[Solana] Photo send failed: {photo_err} — falling back to text")

                if not sent:
                    await asend(text, reply_markup=reply_markup)

                print(f"[Solana] 🆕 Alerted: {display_name} ({short_mint}) | AI {ai_result['score']}/100")

        except Exception as e:
            print(f"[Solana Error] {program_name}: {e}")
