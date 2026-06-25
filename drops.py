import requests
import asyncio
from datetime import datetime, timezone, timedelta
from telegram import Bot

# Fallback import — private config takes priority
try:
    from private.config_live import TELEGRAM_TOKEN, CHAT_ID, ALCHEMY_API_KEY
except ImportError:
    from config import TELEGRAM_TOKEN, CHAT_ID, ALCHEMY_API_KEY

bot = Bot(token=TELEGRAM_TOKEN)

# Track contracts we've already alerted on to avoid duplicates
alerted_contracts = set()

# Minimum mints before we consider it a real drop (filters out test mints)
MIN_MINTS_THRESHOLD = 5

async def send(msg):
    await bot.send_message(chat_id=CHAT_ID, text=msg)

def get_recent_transfers(from_block):
    """
    Fetch recent Transfer events from the zero address (mints)
    across ALL contracts on Ethereum using Alchemy.
    """
    url = f"https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"
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
            "maxCount": "0x32"  # 50 results max
        }]
    }
    res = requests.post(url, json=payload, timeout=15)
    res.raise_for_status()
    return res.json().get("result", {}).get("transfers", [])

def get_current_block():
    """Get the latest Ethereum block number."""
    url = f"https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_blockNumber",
        "params": []
    }
    res = requests.post(url, json=payload, timeout=10)
    res.raise_for_status()
    return int(res.json()["result"], 16)

def get_contract_age_hours(contract_address):
    """
    Check how old a contract is by finding its first transaction.
    Returns age in hours — we only alert on contracts < 24 hours old.
    """
    url = f"https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"
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
    res = requests.post(url, json=payload, timeout=10)
    data = res.json().get("result", {}).get("transfers", [])
    if not data:
        return 999  # Unknown — skip it

    first_tx_time = data[0].get("metadata", {}).get("blockTimestamp", "")
    if not first_tx_time:
        return 999

    first_dt = datetime.fromisoformat(first_tx_time.replace("Z", "+00:00"))
    age_hours = (datetime.now(timezone.utc) - first_dt).total_seconds() / 3600
    return round(age_hours, 1)

def get_mint_count(contract_address):
    """Count total mints for a contract so far."""
    url = f"https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"
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
            "maxCount": "0x64"  # 100 max
        }]
    }
    res = requests.post(url, json=payload, timeout=10)
    transfers = res.json().get("result", {}).get("transfers", [])
    return len(transfers)

def get_nft_standard(contract_address):
    """Detect if contract is ERC-721 or ERC-1155."""
    url = f"https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"
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
    res = requests.post(url, json=payload, timeout=10)
    transfers = res.json().get("result", {}).get("transfers", [])
    if transfers:
        category = transfers[0].get("category", "erc721")
        return "ERC-1155" if category == "erc1155" else "ERC-721"
    return "ERC-721"

# Track the last block we checked
last_checked_block = None

def check_drops():
    global last_checked_block

    print("[Drops] Checking for new NFT drops...")

    try:
        current_block = get_current_block()

        # On first run look back ~100 blocks (~20 mins)
        if last_checked_block is None:
            last_checked_block = current_block - 100

        transfers = get_recent_transfers(last_checked_block)
        last_checked_block = current_block

        if not transfers:
            print("[Drops] No new mint activity detected")
            return

        # Group transfers by contract address
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

            # Skip if we've already alerted on this contract
            if contract in alerted_contracts:
                continue

            # Check contract age — only alert on drops < 24 hours old
            age_hours = get_contract_age_hours(contract)
            if age_hours > 24:
                print(f"[Drops] Skipping {contract[:10]}... — {age_hours}h old")
                continue

            # Check mint count — filter out test deploys
            mint_count = get_mint_count(contract)
            if mint_count < MIN_MINTS_THRESHOLD:
                print(f"[Drops] Skipping {contract[:10]}... — only {mint_count} mints so far")
                continue

            # Detect standard
            standard = get_nft_standard(contract)

            # Mark as alerted
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