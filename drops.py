import requests
import asyncio
from datetime import datetime, timezone, timedelta
from telegram import Bot

try:
    from private.config_live import TELEGRAM_TOKEN, CHAT_ID, ALCHEMY_API_KEY, MIN_MINTS_THRESHOLD
    print("[Drops] ✅ Private config loaded")
except ImportError as e:
    print(f"[Drops] ❌ ImportError: {e}")
    from config import TELEGRAM_TOKEN, CHAT_ID, ALCHEMY_API_KEY, MIN_MINTS_THRESHOLD

bot = Bot(token=TELEGRAM_TOKEN)

# Track contracts we've already alerted on
alerted_contracts = set()

async def send(msg):
    await bot.send_message(chat_id=CHAT_ID, text=msg)

def alchemy_post(payload):
    """Central Alchemy request handler with rate limit and error handling."""
    url = f"https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"
    res = requests.post(url, json=payload, timeout=15)

    if res.status_code == 429:
        print("[Drops] Rate limited by Alchemy — backing off 30 seconds")
        import time; time.sleep(30)
        return None

    res.raise_for_status()
    return res.json()

def get_recent_transfers(from_block):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "alchemy_getAssetTransfers",
        "params": [{
            "fromBlock": hex(from_block),
            "toBlock": "latest",
            "fromAddress": "0x0000000000000000000000000000000000000000",
            "category": ["erc721", "erc1155"],
            "withMetadata": True,
            "maxCount": "0x32"
        }]
    }
    data = alchemy_post(payload)
    if not data:
        return []
    return data.get("result", {}).get("transfers", [])

def get_current_block():
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_blockNumber",
        "params": []
    }
    data = alchemy_post(payload)
    if not data:
        return None
    return int(data["result"], 16)

def get_contract_age_hours(contract_address):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "alchemy_getAssetTransfers",
        "params": [{
            "fromBlock": "0x0",
            "toBlock": "latest",
            "toAddress": contract_address,
            "category": ["erc721", "erc1155"],
            "withMetadata": True,
            "maxCount": "0x1"
        }]
    }
    try:
        data = alchemy_post(payload)
        if not data:
            return 999
        transfers = data.get("result", {}).get("transfers", [])
        if not transfers:
            return 999

        first_tx_time = transfers[0].get("metadata", {}).get("blockTimestamp", "")
        if not first_tx_time:
            return 999

        first_dt = datetime.fromisoformat(first_tx_time.replace("Z", "+00:00"))
        age_hours = (datetime.now(timezone.utc) - first_dt).total_seconds() / 3600
        return round(age_hours, 1)
    except Exception as e:
        print(f"[Drops] Age lookup failed for {contract_address[:10]}...: {e}")
        return 999

def get_mint_count(contract_address):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "alchemy_getAssetTransfers",
        "params": [{
            "fromBlock": "0x0",
            "toBlock": "latest",
            "fromAddress": "0x0000000000000000000000000000000000000000",
            "toAddress": contract_address,
            "category": ["erc721", "erc1155"],
            "maxCount": "0x64"
        }]
    }
    try:
        data = alchemy_post(payload)
        if not data:
            return 0
        return len(data.get("result", {}).get("transfers", []))
    except Exception as e:
        print(f"[Drops] Mint count failed for {contract_address[:10]}...: {e}")
        return 0

def get_nft_standard(contract_address):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "alchemy_getAssetTransfers",
        "params": [{
            "fromBlock": "0x0",
            "toBlock": "latest",
            "fromAddress": "0x0000000000000000000000000000000000000000",
            "category": ["erc721", "erc1155"],
            "maxCount": "0x1"
        }]
    }
    try:
        data = alchemy_post(payload)
        if not data:
            return "ERC-721"
        transfers = data.get("result", {}).get("transfers", [])
        if transfers:
            category = transfers[0].get("category", "erc721")
            return "ERC-1155" if category == "erc1155" else "ERC-721"
    except Exception as e:
        print(f"[Drops] Standard detection failed: {e}")
    return "ERC-721"

# Track the last block we checked
last_checked_block = None

def check_drops():
    global last_checked_block

    print("[Drops] Checking for new NFT drops...")

    try:
        current_block = get_current_block()
        if current_block is None:
            print("[Drops] Could not get current block — skipping this cycle")
            return

        if last_checked_block is None:
            last_checked_block = current_block - 100

        transfers = get_recent_transfers(last_checked_block)
        last_checked_block = current_block

        if not transfers:
            print("[Drops] No new mint activity detected")
            return

        contracts = {}
        for tx in transfers:
            contract = tx.get("rawContract", {}).get("address", "").lower()
            if not contract:
                continue
            if contract not in contracts:
                contracts[contract] = []
            contracts[contract].append(tx)

        print(f"[Drops] Found mint activity on {len(contracts)} contract(s)")

        for contract, txs in contracts.items():
            if contract in alerted_contracts:
                continue

            age_hours = get_contract_age_hours(contract)
            if age_hours > 24:
                print(f"[Drops] Skipping {contract[:10]}... — {age_hours}h old")
                continue

            mint_count = get_mint_count(contract)
            if mint_count < MIN_MINTS_THRESHOLD:
                print(f"[Drops] Skipping {contract[:10]}... — only {mint_count} mints so far")
                continue

            standard = get_nft_standard(contract)
            alerted_contracts.add(contract)
            short_contract = f"{contract[:6]}...{contract[-4:]}"

            asyncio.run(send(
                f"🆕 New NFT Drop Detected!\n"
                f"Contract: {short_contract}\n"
                f"Standard: {standard}\n"
                f"Mints so far: {mint_count}\n"
                f"Age: {age_hours} hours old\n"
                f"🔗 https://opensea.io/assets/ethereum/{contract}/1"
            ))

            print(f"[Drops] 🆕 Alerted: {short_contract} | {standard} | {mint_count} mints | {age_hours}h old")

    except Exception as e:
        print(f"[Drops Error] {e}")