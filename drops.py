import asyncio
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional
import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from notifier import asend, asend_photo, download_image_bytes, escape_html
from gemini_filter import gemini_score_nft, is_worth_alerting, verdict_badge
from deployer_cache import get_contract_creator, get_contract_creation_info, is_known_serial_rugger, record_deployer_result, get_deployer_stats
from dex_liquidity import get_dex_liquidity
from ethos import get_ethos_profile_async, format_telegram_ethos_badge
from metadata_resolver import resolve_metadata_async
import checkpoint

try:
    from private.config_live import MIN_MINTS_THRESHOLD, OPENSEA_API_KEY, GEMINI_MIN_SCORE, MAX_CONTRACT_AGE_HOURS
    print("[Drops] ✅ Private config loaded")
except ImportError as e:
    print(f"[Drops] ❌ ImportError: {e}")
    from config import MIN_MINTS_THRESHOLD, OPENSEA_API_KEY, GEMINI_MIN_SCORE, MAX_CONTRACT_AGE_HOURS

# Track contracts we've already alerted on with bounded cache. The in-memory pair
# is a fast path; checkpoint.py holds the durable copy that survives restarts.
MAX_ALERTED_CONTRACTS = 10000
SEEN_EVM = "evm_contracts"
alerted_contracts_set = set()
alerted_contracts_deque = deque(maxlen=MAX_ALERTED_CONTRACTS)

# A single wallet minting at least this many tokens to itself is bot/self-mint
# churn, not organic demand — skip before spending a Gemini call.
SINGLE_MINTER_MAX_MINTS = 10

# Standard ERC Topics
ERC721_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ERC1155_SINGLE_TOPIC  = "0xc3d58168c5ae7397731d063d5bbf3d657854427343f4c083240f7aacaa2d0f62"
ERC1155_BATCH_TOPIC   = "0x4a39dc06d4c0dbc64b70af90fd698a233a518aa5d07e595d983b8c0526c8f7fb"
ZERO_ADDRESS_TOPIC    = "0x0000000000000000000000000000000000000000000000000000000000000000"

# ── DeFi / Infrastructure Contract Blocklist ──────────────────────────────────
# These are ERC-721 tokens that represent DeFi positions, withdrawal receipts,
# LP tokens, domain registrations, etc. — NOT collectible NFT drops.
DEFI_CONTRACT_BLOCKLIST = {
    # ── Uniswap / DEX Position NFTs ──
    "0xc36442b4a4522e871399cd717abdd847ab11fe88",  # Uniswap V3 Positions NFT-V1 (Ethereum)
    "0xbd216513d74c8cf14cf4747e6aaa6420ff64ee9e",  # Uniswap v4 Positions NFT (Ethereum)
    "0xc36442b4a4522e871399cd717abdd847ab11fe88",  # Uniswap V3 Positions (Polygon/Arb/Opt/Base)
    "0x03a520b32c04bf3beef7beb72e583e96d2541188",  # Uniswap V3 Positions (Base variant)
    "0x00d5bbd0fe14e2e25758a3e0d68cfe26eb2db638",  # Supernova Positions NFT-V1
    "0x827922686190790b37229fd06084350e74485b72",  # Slipstream Position NFT v1 (Base)
    "0xe1f8cd9a83d87b25b0e59790b6a054447cf8f8b3",  # Slipstream Position NFT v1 (Optimism)
    # ── Liquid Staking / Withdrawal Receipts ──
    "0x889edc2edab5f40e902b864ad4d7ade8e412f9b1",  # Lido: stETH Withdrawal NFT
    "0x7d5706f6ef3f89b3951e23e557cdfbc3239d4e2c",  # Lido: Withdraw Request NFT
    "0x8d6fd65050a59e33f44aeae0d6e1f009af65e0a4",  # Kiln Exit Queue
    "0x4bc9fec04f6b81a7f5843f9e72df8c5d0a740544",  # Saturn Withdrawal Request
    "0xf9b179b5f64dbb1b3362a35b1e39e5840b149973",  # Staked ADI
    # ── Domain Name Registrars ──
    "0x57f1887a8bf19b14fc0df6fd9b2acc9af147ea85",  # ENS: Base Registrar
    "0x2a186450130de1e4a90bff6a3a1c8b24d0e75ede",  # ENS: Name Wrapper
    "0x2a18745306aaaecc900e0983c21e8f6c6d53b5b4",  # Decentraland DCL Registrar
    # ── Aave / Lending Receipt Tokens ──
    "0x4d5f47fa6a74757f35c14fd3a6ef8e3c9bc514e8",  # Aave Ethereum aEthWETH
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",  # USDC (ERC-20, extra safety)
    "0x6b175474e89094c44da98b954eedeac495271d0f",  # DAI (ERC-20, extra safety)
    "0xdc035d45d973e3ec169d2276ddab16f1e407384f",  # USDS Stablecoin
}

# Name patterns that indicate DeFi infrastructure, NOT collectible NFTs
DEFI_NAME_PATTERNS = [
    "positions nft",       # Uniswap V3/V4, Supernova, Slipstream, etc.
    "position nft",
    "withdrawal nft",      # Lido, Kiln, Saturn, etc.
    "withdraw request",
    "exit queue",
    "stablecoin",          # USDS, USDC clones
    "wrapped ether",       # WETH impersonators
    "wrapped avax",        # WAVAX
    "wrapped collateral",
    "volatilev2 amm",     # Aerodrome/Velodrome LP tokens
    "stablev2 amm",
    "concentratedliq",
    "pancake lps",         # PancakeSwap LP tokens
    "cake-lp",
    "slipstream position",
    "aave ethereum",       # Aave receipt tokens
    "exactly usdc",        # Exactly Protocol
    "covenant bond",       # DeFi bonds
    "steakhouse usd",
]

def _is_defi_or_infrastructure(contract: str, name: str) -> str | None:
    """Return a skip reason if this contract is DeFi infrastructure, else None."""
    if contract.lower() in DEFI_CONTRACT_BLOCKLIST:
        return "known DeFi/infrastructure contract"
    if name:
        name_lower = name.lower()
        for pattern in DEFI_NAME_PATTERNS:
            if pattern in name_lower:
                return f"DeFi/infrastructure name pattern: '{pattern}'"
    return None

# Multi-Chain Universal EVM Configuration (Tested high-speed public RPCs + Fallbacks)
EVM_CHAINS = {
    "base": {
        "rpcs": [
            "https://mainnet.base.org",
            "https://base.drpc.org",
            "https://base.gateway.tenderly.co",
        ],
        "explorer": "https://basescan.org",
        "opensea_chain": "base",
        "block_step": 60,
    },
    "ethereum": {
        "rpcs": [
            "https://gateway.tenderly.co/public/mainnet",
            "https://eth.drpc.org",
            "https://rpc.builder0x69.io",
            "https://rpc.mevblocker.io",
        ],
        "explorer": "https://etherscan.io",
        "opensea_chain": "ethereum",
        "block_step": 30,
    },
    "polygon": {
        "rpcs": [
            "https://polygon.drpc.org",
            "https://polygon.gateway.tenderly.co",
        ],
        "explorer": "https://polygonscan.com",
        "opensea_chain": "matic",
        "block_step": 50,
    },
    "arbitrum": {
        "rpcs": [
            "https://arb1.arbitrum.io/rpc",
            "https://arbitrum.drpc.org",
            "https://arbitrum.gateway.tenderly.co",
        ],
        "explorer": "https://arbiscan.io",
        "opensea_chain": "arbitrum",
        "block_step": 60,
    },
    "optimism": {
        "rpcs": [
            "https://mainnet.optimism.io",
            "https://optimism.drpc.org",
            "https://optimism.gateway.tenderly.co",
        ],
        "explorer": "https://optimistic.etherscan.io",
        "opensea_chain": "optimism",
        "block_step": 60,
    },
    "bsc": {
        "rpcs": [
            "https://bsc.rpc.blxrbdn.com",
            "https://1rpc.io/bnb",
            "https://bsc.drpc.org",
        ],
        "explorer": "https://bscscan.com",
        "opensea_chain": "bsc",
        "block_step": 20,
    },
    "avalanche": {
        "rpcs": [
            "https://api.avax.network/ext/bc/C/rpc",
            "https://avalanche.drpc.org",
            "https://avax.meowrpc.com",
        ],
        "explorer": "https://snowtrace.io",
        "opensea_chain": "avalanche",
        "block_step": 50,
    },
    "robinhood": {
        "rpcs": [
            "https://rpc.mainnet.chain.robinhood.com",
        ],
        "explorer": "https://robinhoodchain.blockscout.com",
        "opensea_chain": None,
        "block_step": 60,
    },
}

# ── RPC endpoint health probing ───────────────────────────────────────────────
# Endpoint order is a latency preference, but a preference is worthless if the
# preferred endpoint cannot answer. Three failure modes were live in production
# and all three cost a full timeout before failover, every cycle:
#
#   HTTP 429  Alchemy monthly capacity exhausted   (ethereum, base, arbitrum)
#   HTTP 403  network not enabled for the app      (polygon, optimism)
#   HTTP 500  provider broken                      (mainnet.base.org, mainnet.optimism.io)
#
# So ordering is decided by a one-off health probe at startup rather than by
# configuration. Unhealthy endpoints are demoted to the back rather than dropped,
# so a transient failure at startup cannot permanently lose an endpoint.
#
# Alchemy used to be prepended to five chains here. It was removed after a live
# probe: ethereum, base and arbitrum returned HTTP 429 "Monthly capacity limit
# exceeded", while polygon and optimism returned HTTP 403 "not enabled for this
# app", meaning those two never worked at all. No Alchemy enhanced API was ever
# used (no getAssetTransfers, no nft/v3, no alchemy_* methods) - only standard
# JSON-RPC that the public endpoints serve with byte-identical results.

# Probe latency, measured against real public endpoints under concurrency:
# base.drpc.org and base.gateway.tenderly.co both needed ~11.5s. A 6s timeout
# reported healthy endpoints as failures, which emptied the healthy list for that
# chain and let a refusing endpoint keep first position.
PROBE_TIMEOUT = 15
# Hard wall-clock cap on the whole startup probe. requests' `timeout` is an
# inter-byte read timeout, not a total duration, so a provider that dribbles a
# 15k-log response back can hold a single probe open far longer (38.7s observed).
# Probing serially cost 390s of startup, delaying the first scan by 6.5 minutes.
PROBE_TOTAL_BUDGET = 75
# 12 parallel eth_getLogs calls saturated a home connection and manufactured the
# very timeouts the probe was meant to detect. Modest concurrency is more accurate.
PROBE_CONCURRENCY = 6


def probe_rpc_endpoint(url: str, timeout: int = PROBE_TIMEOUT,
                       block_step: Optional[int] = None) -> Optional[bool]:
    """Probe an endpoint with the query the bot actually makes.

    Returns a tri-state, because "refused" and "unreachable" are different facts
    and collapsing them promotes broken endpoints:

        True  - served eth_getLogs. Usable.
        False - answered and refused: HTTP 429/403/500, or a JSON-RPC error body.
                A verdict from the endpoint itself.
        None  - no answer: timeout, DNS failure, connection refused. Says nothing
                about the endpoint, so it must not be treated as a verdict.

    Probes eth_getLogs, not eth_blockNumber. Both mainnet.base.org and
    mainnet.optimism.io answer eth_blockNumber with HTTP 200 and then fail
    eth_getLogs with "backend response too large" (base took 50.9s to do it), so
    a liveness check on the cheap method selects for endpoints that cannot work.

    Deliberately strict about error bodies: providers signal quota and range
    limits inside a 200 response.
    """
    if not url or "YOUR_KEY" in url:
        return False

    step = block_step if block_step else 30
    try:
        res = requests.post(
            url,
            json={"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []},
            timeout=timeout,
        )
    except Exception:
        return None          # never reached it
    if res.status_code != 200:
        return False         # it answered, and refused
    try:
        tip = int(res.json()["result"], 16)
    except Exception:
        return False         # answered with something unusable

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_getLogs",
        "params": [{
            "fromBlock": hex(max(tip - step, 0)),
            "toBlock": hex(tip),
            "topics": [ERC721_TRANSFER_TOPIC],
        }],
    }
    try:
        res = requests.post(url, json=payload, timeout=timeout)
    except Exception:
        return None
    if res.status_code != 200:
        return False
    try:
        data = res.json()
    except Exception:
        return False
    if not isinstance(data, dict) or "error" in data:
        return False
    return isinstance(data.get("result"), list)


def reorder_rpcs_by_health(rpcs, probe=probe_rpc_endpoint, block_step: Optional[int] = None,
                           health=None):
    """Order endpoints by what we actually learned about them.

    Three tiers, each preserving configured order within itself:

        healthy (True)   - proven able to serve a scan
        unknown (None)   - never reached, so no evidence either way
        refusing (False) - answered and refused; a definitive verdict

    A refusing endpoint sinks below an unknown one. That direction matters: an
    over-quota endpoint that answers 429 can never serve a scan, while a timeout
    may be a transient network problem on our side.

    Nothing is ever dropped. If we learned nothing at all, configured order is
    returned untouched rather than invented.

    `health` accepts a precomputed {url: True|False|None} map so callers can probe
    every chain concurrently and reuse the verdicts here.
    """
    seen = set()
    unique = []
    for url in rpcs:
        if url not in seen:
            seen.add(url)
            unique.append(url)

    def verdict(url):
        if health is not None:
            return health.get(url)
        try:
            return probe(url, block_step=block_step)
        except TypeError:
            return probe(url)

    healthy, unknown, refusing = [], [], []
    for url in unique:
        v = verdict(url)
        (healthy if v is True else refusing if v is False else unknown).append(url)

    if not healthy and not refusing:
        print("[Drops] ⚠️  Health probe was inconclusive for every endpoint; "
              "keeping configured order")
        return unique
    if not healthy:
        print("[Drops] ⚠️  No endpoint passed the health probe; "
              "runtime failover will pick a working one")
    # refusing goes last unconditionally, including when every other endpoint for
    # this chain went unprobed. A 429 is a fact; an unprobed endpoint is a maybe,
    # and a maybe outranks a known no.
    return healthy + unknown + refusing


def wire_healthy_rpcs(chains=None, probe=probe_rpc_endpoint,
                      budget: int = PROBE_TOTAL_BUDGET):
    """Probe every chain's endpoints concurrently at startup and order by what works.

    Adds the Alchemy URL as a candidate when a key is configured, but it has to
    pass the same probe as everything else to earn first position. If the key
    later starts working, a restart promotes it automatically.

    Probes run in parallel under a hard wall-clock budget: endpoint health is
    transient (mainnet.optimism.io failed and then passed 20 minutes later), so
    this is a best-effort startup optimization, not a correctness guarantee. The
    per-request failover in direct_rpc_post remains the actual safety net, which
    is why an unfinished probe is safe to abandon.
    """
    targets = list(EVM_CHAINS.keys()) if chains is None else chains

    candidates = {}
    for chain in targets:
        cfg = EVM_CHAINS.get(chain)
        if not cfg:
            continue
        candidates[chain] = list(cfg["rpcs"])

    jobs = {url: EVM_CHAINS[chain].get("block_step")
            for chain, urls in candidates.items() for url in urls}

    health = {}
    deadline = time.monotonic() + budget
    with ThreadPoolExecutor(max_workers=PROBE_CONCURRENCY) as pool:
        futures = {}
        for url, step in jobs.items():
            try:
                futures[pool.submit(probe, url, block_step=step)] = url
            except TypeError:
                futures[pool.submit(probe, url)] = url
        for future in as_completed(futures, timeout=None):
            url = futures[future]
            try:
                result = future.result(timeout=0)
            except Exception:
                # Could not determine health: treat as untested rather than
                # unhealthy, so a probe error cannot demote a working endpoint.
                result = None
            # Preserve the tri-state: True/False are verdicts, None means we
            # learned nothing. Coercing None to False promotes broken endpoints.
            health[url] = result if result is None else bool(result)
            if time.monotonic() > deadline:
                break

        # The budget stops us waiting, but any probe that already finished has a
        # real verdict worth keeping. Discarding those let a known-429 endpoint
        # look merely "unprobed" and reclaim first place on a chain whose other
        # endpoints were still in flight.
        for future, url in futures.items():
            if url in health or not future.done():
                continue
            try:
                result = future.result(timeout=0)
            except Exception:
                result = None
            health[url] = result if result is None else bool(result)

    unprobed = [u for u in jobs if u not in health]
    if unprobed:
        print(f"[Drops] ⏱  probe budget {budget}s reached; {len(unprobed)} endpoint(s) "
              f"left unprobed and keep their configured position")
        for url in unprobed:
            health[url] = None

    for chain, urls in candidates.items():
        ordered = reorder_rpcs_by_health(urls, health=health)
        EVM_CHAINS[chain]["rpcs"] = ordered
        first = ordered[0].split("//")[-1][:28]
        verdict = health.get(ordered[0])
        tag = "verified" if verdict is True else "unverified"
        print(f"[Drops] RPC {chain}: preferring {first} ({tag})")


ALL_SUPPORTED_CHAINS = list(EVM_CHAINS.keys())

# Block watermarks resume from the persistent checkpoint. Previously this dict was
# seeded with None on every import, so each restart re-seeded from the current
# chain tip: any blocks produced while the bot was down were skipped entirely, and
# the freshly-scanned window re-alerted contracts whose in-memory dedup set had
# just been wiped. Loading from disk fixes both directions.
last_checked_blocks = {
    chain: checkpoint.get_block(chain) for chain in ALL_SUPPORTED_CHAINS
}


def direct_rpc_post(chain: str, payload: dict):
    """
    Direct JSON-RPC handler with automatic RPC failover and retry logic.
    """
    chain_config = EVM_CHAINS.get(chain)
    if not chain_config:
        raise ValueError(f"[Drops] Unknown chain: {chain}")

    rpcs = chain_config["rpcs"]
    for rpc_url in rpcs:
        if not rpc_url or "YOUR_KEY" in rpc_url:
            continue
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


def rpc_post(chain: str, payload: dict):
    """Unified RPC dispatcher for all EVM chains."""
    return direct_rpc_post(chain, payload)


async def get_current_block(chain: str):
    """Fetch the latest block number for the specified chain."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_blockNumber",
        "params": []
    }
    data = await asyncio.to_thread(rpc_post, chain, payload)
    if not data or "result" not in data:
        return None
    try:
        return int(data["result"], 16)
    except Exception:
        return None


# ── True contract-age detection ──────────────────────────────────────────────
# The mint-window age (get_contract_data_batched) can't tell an old-but-active
# collection from a fresh drop. We derive the CONTRACT's real deployment time
# from the explorer's creation record (reusing get_contract_creation_info) and
# cache it per contract, bounded so it can't grow without limit.
MAX_DEPLOY_TS_CACHE = 5000
contract_deploy_ts_cache = {}
_deploy_ts_cache_order = deque(maxlen=MAX_DEPLOY_TS_CACHE)


def _cache_deploy_ts(key: str, ts):
    """Store a deployment timestamp (or None) with bounded eviction."""
    if key in contract_deploy_ts_cache:
        return
    if len(_deploy_ts_cache_order) == MAX_DEPLOY_TS_CACHE:
        oldest = _deploy_ts_cache_order.popleft()
        contract_deploy_ts_cache.pop(oldest, None)
    contract_deploy_ts_cache[key] = ts
    _deploy_ts_cache_order.append(key)


async def _resolve_deploy_timestamp(chain: str, info: dict):
    """Resolve a contract's deployment unix timestamp from explorer creation info,
    falling back to RPC (block -> timestamp, or txHash -> block -> timestamp).
    Returns an int timestamp, or None if it can't be determined."""
    if info.get("deploy_ts"):
        return info["deploy_ts"]

    deploy_block = info.get("deploy_block")
    tx_hash = info.get("tx_hash")

    # txHash -> block number
    if deploy_block is None and tx_hash:
        tx_payload = {
            "jsonrpc": "2.0", "id": 1, "method": "eth_getTransactionByHash",
            "params": [tx_hash],
        }
        try:
            tx_data = await asyncio.to_thread(rpc_post, chain, tx_payload)
            result = tx_data.get("result") if isinstance(tx_data, dict) else None
            block_hex = result.get("blockNumber") if isinstance(result, dict) else None
            if block_hex:
                deploy_block = int(block_hex, 16)
        except Exception:
            pass

    # block number -> timestamp
    if deploy_block is not None:
        block_payload = {
            "jsonrpc": "2.0", "id": 2, "method": "eth_getBlockByNumber",
            "params": [hex(deploy_block), False],
        }
        try:
            block_data = await asyncio.to_thread(rpc_post, chain, block_payload)
            if block_data and block_data.get("result"):
                return int(block_data["result"].get("timestamp", "0x0"), 16)
        except Exception:
            pass

    return None


async def get_contract_age_hours(chain: str, contract: str, info: dict):
    """Return the contract's TRUE age in hours since deployment, or None if the
    explorer/RPC can't tell us. Cached per contract (result reused, even None)."""
    key = f"{chain}:{contract.lower()}"
    if key in contract_deploy_ts_cache:
        ts = contract_deploy_ts_cache[key]
    else:
        ts = await _resolve_deploy_timestamp(chain, info)
        _cache_deploy_ts(key, ts)
    if not ts:
        return None
    return round((datetime.now(timezone.utc).timestamp() - ts) / 3600, 1)


def get_explorer_url(chain: str, contract: str) -> str:
    """Return the correct block explorer contract URL."""
    config = EVM_CHAINS.get(chain, {})
    explorer = config.get("explorer", "https://etherscan.io")
    return f"{explorer}/address/{contract}"


def get_opensea_url(contract: str, chain: str = "ethereum") -> str:
    """Return OpenSea URL if supported on this chain."""
    config = EVM_CHAINS.get(chain, {})
    os_chain = config.get("opensea_chain")
    if os_chain:
        return f"https://opensea.io/assets/{os_chain}/{contract}/1"
    return f"https://opensea.io/assets/{chain}/{contract}/1"


async def get_recent_transfers(chain: str, from_block: int, to_block: int):
    """
    Standard eth_getLogs for all chains detecting both ERC-721 and ERC-1155 mint events.
    Scans the explicit [from_block, to_block] range (never "latest") so callers can
    advance their watermark to exactly to_block with no overlap / re-scan.
    Detects:
      - ERC-721:  Transfer(0x0, to, tokenId)
      - ERC-1155: TransferSingle(operator, 0x0, to, id, value)
    """
    payload_721 = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_getLogs",
        "params": [{
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
            "topics": [
                ERC721_TRANSFER_TOPIC,
                ZERO_ADDRESS_TOPIC,  # from == 0x0 (mint)
            ]
        }]
    }

    payload_1155 = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "eth_getLogs",
        "params": [{
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
            "topics": [
                ERC1155_SINGLE_TOPIC,
                None,
                ZERO_ADDRESS_TOPIC,  # from == 0x0 (mint)
            ]
        }]
    }

    data_721 = await asyncio.to_thread(rpc_post, chain, payload_721)
    data_1155 = await asyncio.to_thread(rpc_post, chain, payload_1155)

    if data_721 is None and data_1155 is None:
        return None

    transfers = []
    if data_721 and "result" in data_721 and isinstance(data_721["result"], list):
        for log in data_721["result"]:
            # ERC-721 Transfer MUST have 4 topics: [Transfer, from, to, tokenId]
            # ERC-20 Transfer only has 3 topics: [Transfer, from, to]
            if len(log.get("topics", [])) == 4:
                transfers.append({
                    "rawContract": {"address": log.get("address", "").lower()},
                    "blockNumber": log.get("blockNumber"),
                    "transactionHash": log.get("transactionHash"),
                    "standard": "ERC-721",
                })

    if data_1155 and "result" in data_1155 and isinstance(data_1155["result"], list):
        for log in data_1155["result"]:
            # ERC-1155 TransferSingle has 4 topics: [TransferSingle, operator, from, to]
            if len(log.get("topics", [])) >= 4:
                transfers.append({
                    "rawContract": {"address": log.get("address", "").lower()},
                    "blockNumber": log.get("blockNumber"),
                    "transactionHash": log.get("transactionHash"),
                    "standard": "ERC-1155",
                })

    return transfers


async def get_contract_data_batched(chain: str, contract_address: str, batch_txs: list = None):
    """
    RPC-safe contract inspection using bounded block ranges.

    Uses a 1000-block lookback window instead of fromBlock: 0x0 to avoid
    public RPC block-range limits. Falls back to batch_txs if targeted scan fails.
    """
    # ── Step 1: Get current block for bounded range ──────────────────
    current_block = await get_current_block(chain)
    lookback_blocks = 1000
    from_block = max(0, current_block - lookback_blocks) if current_block else 0

    mint_logs = []
    standard = "ERC-721"
    if batch_txs and batch_txs[0].get("standard") == "ERC-1155":
        standard = "ERC-1155"

    # ── Step 2: Bounded ERC-721 mint scan ────────────────────────────
    if standard == "ERC-721":
        payload = {
            "jsonrpc": "2.0", "id": 1, "method": "eth_getLogs",
            "params": [{
                "fromBlock": hex(from_block), "toBlock": "latest",
                "address": contract_address,
                "topics": [ERC721_TRANSFER_TOPIC, ZERO_ADDRESS_TOPIC],
            }]
        }
        try:
            data = await asyncio.to_thread(rpc_post, chain, payload)
            if data and data.get("result"):
                mint_logs = [l for l in data["result"] if len(l.get("topics", [])) == 4]
        except Exception:
            mint_logs = []

    # ── Step 3: Check ERC-1155 if no ERC-721 mints found ─────────────
    if not mint_logs:
        erc1155_payload = {
            "jsonrpc": "2.0", "id": 2, "method": "eth_getLogs",
            "params": [{
                "fromBlock": hex(from_block),
                "toBlock": "latest",
                "address": contract_address,
                "topics": [ERC1155_SINGLE_TOPIC, None, ZERO_ADDRESS_TOPIC],
            }]
        }
        try:
            erc1155_data = await asyncio.to_thread(rpc_post, chain, erc1155_payload)
            if erc1155_data and erc1155_data.get("result"):
                standard = "ERC-1155"
                mint_logs = [l for l in erc1155_data["result"] if len(l.get("topics", [])) >= 4]
        except Exception:
            pass

    mint_count = len(mint_logs)

    # ── Step 4: Fallback to batch_txs if RPC scan returned nothing ───
    if mint_count == 0 and batch_txs:
        mint_count = len(batch_txs)

    # ── Step 5: Unique minters from topic[2] (or topic[3] for 1155) ───
    unique_minters = 0
    if mint_logs:
        topic_idx = 2 if standard == "ERC-721" else 3
        unique_minters = len({
            log["topics"][min(topic_idx, len(log.get("topics", [])) - 1)]
            for log in mint_logs
            if len(log.get("topics", [])) > 2
        })
    elif batch_txs:
        unique_minters = len({
            tx.get("transactionHash", "")
            for tx in batch_txs
            if tx.get("transactionHash")
        })

    # ── Step 6: Contract age from earliest mint block timestamp ──────
    age_hours = 999
    earliest_block_hex = None
    if mint_logs:
        earliest_block_hex = mint_logs[0].get("blockNumber")
    elif batch_txs:
        earliest_block_hex = batch_txs[0].get("blockNumber")

    if earliest_block_hex:
        block_payload = {
            "jsonrpc": "2.0", "id": 3, "method": "eth_getBlockByNumber",
            "params": [earliest_block_hex, False]
        }
        try:
            block_data = await asyncio.to_thread(rpc_post, chain, block_payload)
            if block_data and block_data.get("result"):
                ts = int(block_data["result"].get("timestamp", "0x0"), 16)
                age_hours = round((datetime.now(timezone.utc).timestamp() - ts) / 3600, 1)
        except Exception:
            pass

    return {
        "mint_count": mint_count,
        "age_hours": age_hours,
        "standard": standard,
        "unique_minters": unique_minters,
        "earliest_block": int(earliest_block_hex, 16) if earliest_block_hex else None,
    }


def _decode_abi_string(hex_str: str) -> str:
    """Safely decode an ABI-encoded string returned by eth_call."""
    try:
        if not hex_str or hex_str == "0x":
            return ""
        clean = hex_str[2:] if hex_str.startswith("0x") else hex_str
        if len(clean) >= 128:
            length = int(clean[64:128], 16)
            data_hex = clean[128:128 + length * 2]
            return bytes.fromhex(data_hex).decode("utf-8", errors="ignore").strip()
        return bytes.fromhex(clean).decode("utf-8", errors="ignore").replace("\x00", "").strip()
    except Exception:
        return ""


async def get_contract_name_and_symbol(chain: str, contract_address: str):
    """Query name() and symbol() via direct eth_call."""
    NAME_SIG = "0x06fdde03"
    SYMBOL_SIG = "0x95d89b41"
    name = ""
    symbol = ""

    try:
        payload = {
            "jsonrpc": "2.0", "id": 1, "method": "eth_call",
            "params": [{"to": contract_address, "data": NAME_SIG}, "latest"]
        }
        res = await asyncio.to_thread(rpc_post, chain, payload)
        if res and res.get("result"):
            name = _decode_abi_string(res["result"])
    except Exception:
        pass

    try:
        payload = {
            "jsonrpc": "2.0", "id": 1, "method": "eth_call",
            "params": [{"to": contract_address, "data": SYMBOL_SIG}, "latest"]
        }
        res = await asyncio.to_thread(rpc_post, chain, payload)
        if res and res.get("result"):
            symbol = _decode_abi_string(res["result"])
    except Exception:
        pass

    return name, symbol


async def get_contract_token_uri(chain: str, contract_address: str):
    """Query tokenURI(1) or uri(1) via direct eth_call."""
    TOKEN_URI_CALL = "0xc87b56dd0000000000000000000000000000000000000000000000000000000000000001"
    try:
        payload = {
            "jsonrpc": "2.0", "id": 1, "method": "eth_call",
            "params": [{"to": contract_address, "data": TOKEN_URI_CALL}, "latest"]
        }
        res = await asyncio.to_thread(rpc_post, chain, payload)
        if res and res.get("result"):
            return _decode_abi_string(res["result"])
    except Exception:
        pass
    return ""


# ── Minted-out detection ─────────────────────────────────────────────────────
# A collection that has already sold out is not actionable: by the time we alert,
# there is nothing left to mint. We compare the supply already minted against the
# collection's declared cap via eth_call. Contracts vary wildly in which getters
# they expose, so we try the common ones and treat "no cap found" as "unknown"
# rather than "minted out" (never block a drop on missing data).
#
# Every selector below was derived with Keccak-256 and cross-checked against the
# already-trusted name()/symbol() selectors. Do NOT add a guessed selector: a
# wrong 4-byte value can collide with an unrelated function and return a bogus
# number that reads as a real cap, which would silently suppress live drops.
# Verified live: BAYC exposes MAX_APES, Pudgy Penguins exposes MAX_ELEMENTS,
# Doodles exposes MAX_SUPPLY. ERC721A collections (Azuki, Moonbirds, Milady) and
# MAYC expose no cap getter at all and correctly fall through to "unknown".
#
# KNOWN LIMITATION: a collection can be sold out in reality while publishing no
# cap on-chain (MAYC is 19,569 minted with no readable cap of any kind). Those
# stay alertable. That is deliberate - the alternative is inferring a cap from
# stalled mint activity, which would suppress genuine slow-minting drops. The
# true-age gate (MAX_CONTRACT_AGE_HOURS) already filters most of these out.
SUPPLY_SELECTORS = [
    ("totalSupply", "0x18160ddd"),
    ("totalMinted", "0xa2309ff8"),
]
MAX_SUPPLY_SELECTORS = [
    ("maxSupply", "0xd5abeb01"),
    ("MAX_SUPPLY", "0x32cb6b0c"),
    ("maxTotalSupply", "0x2ab4d052"),
    ("MAX_TOKENS", "0xf47c84c5"),
    ("collectionSize", "0x45c0f533"),
    ("MAX_ELEMENTS", "0x3502a716"),
    ("MAX_NFT_SUPPLY", "0xb5077f44"),
    ("MAX_APES", "0xbb8a16bd"),
]

# Treat a collection as minted out at this fraction of its cap. Slightly below
# 1.0 because the final few tokens are often reserved for the team, so a
# collection stuck at 9,995/10,000 is effectively closed to the public.
MINTED_OUT_RATIO = 0.995

# Reject a "cap" that the minted supply overshoots by more than this factor. A
# selector collision on an unrelated function can return a small bogus number
# (observed live: a wrong selector returned 32 for a 1.2M-supply contract), and
# reading that as a cap would wrongly suppress an actively minting drop. Real
# sold-out collections sit at or just above their cap, never multiples of it.
MAX_CREDIBLE_OVERSHOOT = 1.05


def _decode_abi_uint(hex_str: str):
    """Decode a single ABI-encoded uint256 from an eth_call result, or None."""
    if not hex_str:
        return None
    clean = hex_str[2:] if hex_str.startswith("0x") else hex_str
    if not clean:
        return None
    try:
        value = int(clean[:64], 16)
    except ValueError:
        return None
    # Reject absurd sentinels: many contracts return 2**256-1 for "uncapped",
    # which must not be read as a real cap.
    if value >= (1 << 255):
        return None
    return value


async def _call_uint(chain: str, contract_address: str, selector: str):
    """eth_call a no-arg uint256 getter. Returns None when absent or reverting."""
    try:
        payload = {
            "jsonrpc": "2.0", "id": 1, "method": "eth_call",
            "params": [{"to": contract_address, "data": selector}, "latest"],
        }
        res = await asyncio.to_thread(rpc_post, chain, payload)
        if res and res.get("result"):
            return _decode_abi_uint(res["result"])
    except Exception:
        pass
    return None


async def get_supply_info(chain: str, contract_address: str) -> dict:
    """Resolve minted supply vs declared cap for a contract.

    Returns::

        {"minted": int|None, "max_supply": int|None,
         "is_minted_out": bool, "reason": str}

    ``is_minted_out`` is only True when BOTH numbers were resolved and minted has
    reached MINTED_OUT_RATIO of the cap. Missing data yields False so an
    unreadable contract is never blocked on this check alone.
    """
    info = {"minted": None, "max_supply": None, "is_minted_out": False, "reason": ""}

    for _, selector in SUPPLY_SELECTORS:
        value = await _call_uint(chain, contract_address, selector)
        if value is not None:
            info["minted"] = value
            break

    for _, selector in MAX_SUPPLY_SELECTORS:
        value = await _call_uint(chain, contract_address, selector)
        # A zero cap means "not configured", not "sold out".
        if value is not None and value > 0:
            info["max_supply"] = value
            break

    minted = info["minted"]
    cap = info["max_supply"]
    if minted is not None and cap:
        # Guard against a selector collision returning a nonsense cap. If minted
        # supply wildly exceeds the "cap", the cap is not a cap - discard it and
        # fail open rather than suppressing an actively minting collection.
        if minted > cap * MAX_CREDIBLE_OVERSHOOT:
            info["max_supply"] = None
            info["reason"] = f"{minted} minted, declared cap {cap} not credible (ignored)"
            return info
        if minted >= cap * MINTED_OUT_RATIO:
            info["is_minted_out"] = True
            info["reason"] = f"minted out ({minted}/{cap})"
        else:
            pct = round(minted / cap * 100, 1)
            info["reason"] = f"{minted}/{cap} minted ({pct}%)"
    return info


async def get_verified_contract_source(chain: str, contract_address: str) -> str:
    """Fetch verified source code snippet if available from Blockscout or Etherscan API."""
    config = EVM_CHAINS.get(chain, {})
    explorer = config.get("explorer", "")
    if not explorer:
        return ""

    try:
        api_url = f"{explorer}/api?module=contract&action=getsourcecode&address={contract_address}"
        res = await asyncio.to_thread(requests.get, api_url, timeout=4)
        if res.status_code == 200:
            data = res.json()
            if data.get("status") == "1" and data.get("result"):
                source_data = data["result"][0]
                source_code = source_data.get("SourceCode", "")
                if source_code:
                    return source_code[:1500]
    except Exception:
        pass
    return ""


def _remember_contract(contract: str):
    """Record a contract as handled, in memory and in the persistent store.

    Used both after a successful alert and after a terminal skip (minted out), so
    a restart does not re-evaluate work already completed.
    """
    if len(alerted_contracts_deque) == MAX_ALERTED_CONTRACTS:
        oldest = alerted_contracts_deque.popleft()
        alerted_contracts_set.discard(oldest)
    if contract not in alerted_contracts_set:
        alerted_contracts_set.add(contract)
        alerted_contracts_deque.append(contract)
    checkpoint.mark_seen(SEEN_EVM, contract)


async def evaluate_contract_drop(chain: str, contract: str, txs: list, semaphore: asyncio.Semaphore):
    """Evaluate and alert for a single contract drop with concurrency limiting."""
    async with semaphore:
        # Dedup check spans restarts: the persisted store is authoritative, the
        # in-memory set is just a fast path.
        if contract in alerted_contracts_set or checkpoint.was_seen(SEEN_EVM, contract):
            return

        short_contract = f"{contract[:6]}...{contract[-4:]}"

        # ── DeFi / Infrastructure Fast-Skip (address blocklist) ───────
        # Reject known LP / position / withdrawal-receipt contracts before
        # any RPC or Gemini spend — these are not collectible NFT drops.
        skip_reason = _is_defi_or_infrastructure(contract, "")
        if skip_reason:
            print(f"[Drops] ⏭️ Skipped {short_contract} on {chain}: {skip_reason}")
            return

        # ── Batched Data Collection ──────────────────────────────────
        cd = await get_contract_data_batched(chain, contract, batch_txs=txs)
        age_hours      = cd["age_hours"]
        mint_count     = cd["mint_count"]
        standard       = cd["standard"]
        unique_minters = cd["unique_minters"]
        earliest_block = cd.get("earliest_block")

        # ── Basic Filters ─────────────────────────────────────────────
        if age_hours > 24:
            print(f"[Drops] ⏭️ Skipped {short_contract} on {chain}: too old ({age_hours}h)")
            return

        if mint_count < MIN_MINTS_THRESHOLD:
            print(f"[Drops] ⏭️ Skipped {short_contract} on {chain}: low mints ({mint_count} < {MIN_MINTS_THRESHOLD})")
            return

        # ── Self-mint / bot-churn Fast-Skip ───────────────────────────
        # One wallet minting a pile of tokens to itself is a bot/self-mint,
        # not real demand. Skip before spending a Gemini call (Gemini would
        # only flag it as LIKELY_RUG anyway). Not added to the alerted set,
        # so it can still be re-evaluated later if more minters join.
        if unique_minters == 1 and mint_count >= SINGLE_MINTER_MAX_MINTS:
            print(f"[Drops] ⏭️ Skipped {short_contract} on {chain}: single-wallet self-mint ({mint_count} mints, 1 minter)")
            return

        print(f"[Drops] ✅ {short_contract} on {chain} passed filters: {mint_count} mints, {age_hours}h old, {unique_minters} minters ({standard})")

        # ── Minted-Out Gate ──────────────────────────────────────────
        # A sold-out collection is not actionable - there is nothing left to
        # mint by the time the alert lands. Checked before the explorer/Gemini
        # spend. Recorded as seen so we don't re-evaluate it every cycle while
        # it keeps emitting secondary transfers.
        supply_info = await get_supply_info(chain, contract)
        if supply_info["is_minted_out"]:
            print(f"[Drops] ⏭️ Skipped {short_contract} on {chain}: {supply_info['reason']}")
            _remember_contract(contract)
            return

        # ── Deployer Resolution + TRUE deployment age (RPC) ────────────
        creation_info = await get_contract_creation_info(
            chain, contract, EVM_CHAINS, known_block=earliest_block)
        deployer_addr = creation_info.get("creator", "")

        # True-age gate: skip collections whose CONTRACT was deployed long ago,
        # even if they're minting right now. The mint-window age above can't catch
        # these — an old open-edition still minting always looks minutes-old. When
        # RPC can't give us a deploy timestamp, true_age is None and we fall back
        # to the mint-window age check above (don't over-block).
        true_age_hours = await get_contract_age_hours(chain, contract, creation_info)
        if true_age_hours is not None and true_age_hours > MAX_CONTRACT_AGE_HOURS:
            print(f"[Drops] ⏭️ Skipped {short_contract} on {chain}: contract too old (deployed {true_age_hours}h ago)")
            return

        is_rugger, rug_reason = is_known_serial_rugger(deployer_addr)
        if is_rugger:
            print(f"[Drops] 🚫 Serial rugger blocked {short_contract}: {rug_reason}")
            return

        # ── Ethos Network Reputation Layer ────────────────────────────
        ethos_profile = await get_ethos_profile_async(deployer_addr)
        if ethos_profile.get("is_flagged"):
            print(f"[Drops] 🚫 Ethos flagged creator for {short_contract} ({deployer_addr[:10]}...)")
            return

        # ── Rich Context (Names, Decentralized Metadata, Image, DEX) ──
        name, symbol = await get_contract_name_and_symbol(chain, contract)

        # ── DeFi / Infrastructure Fast-Skip (name patterns) ───────────
        # With the on-chain name known, skip protocol position/LP tokens
        # (Uniswap/Pancake/Slipstream positions, etc.) before the metadata,
        # source, DEX, and Gemini calls.
        skip_reason = _is_defi_or_infrastructure(contract, name)
        if skip_reason:
            print(f"[Drops] ⏭️ Skipped {name or short_contract} on {chain}: {skip_reason}")
            return

        token_uri = await get_contract_token_uri(chain, contract)
        
        # Resolve metadata directly from IPFS/Arweave/HTTP/Base64
        metadata = await resolve_metadata_async(token_uri) if token_uri else {}
        if not name and metadata.get("name"):
            name = metadata["name"]
        
        image_url = metadata.get("image_url")
        verified_source = await get_verified_contract_source(chain, contract)
        dex_info = await get_dex_liquidity(chain, contract)
        deployer_stats = get_deployer_stats(deployer_addr)

        # ── Gemini AI Audit ───────────────────────────────────────────
        mint_velocity = round(mint_count / max(age_hours, 0.1), 1)
        ai_result = await gemini_score_nft({
            "contract":                 contract,
            "chain":                    chain,
            "name":                     name,
            "symbol":                   symbol,
            "mint_count":               mint_count,
            "age_hours":                age_hours,
            "standard":                 standard,
            "unique_minters":           unique_minters,
            "mint_velocity_per_hour":   mint_velocity,
            "token_uri":                token_uri,
            "metadata":                 metadata,
            "verified_source_snippet": verified_source,
            "deployer_address":         deployer_addr,
            "deployer_stats":           deployer_stats,
            "ethos_profile":            ethos_profile,
            "dex_liquidity":            dex_info,
        })

        # Record deployer outcome
        if deployer_addr:
            record_deployer_result(
                deployer_address=deployer_addr,
                contract_address=contract,
                score=ai_result.get("score", 50),
                verdict=ai_result.get("verdict", "UNKNOWN")
            )

        if not is_worth_alerting(ai_result, GEMINI_MIN_SCORE):
            print(f"[Drops] 🚫 Gemini blocked {name or short_contract}: {ai_result['reason']} ({ai_result['score']}/100)")
            return

        # ── Dedup ─────────────────────────────────────────────────────
        # Marked BEFORE the send so a crash mid-send cannot produce a duplicate
        # alert on restart. Losing one alert is preferable to spamming the chat.
        _remember_contract(contract)

        # ── Build Telegram Buttons ────────────────────────────────────
        explorer_link = get_explorer_url(chain, contract)
        opensea_link = get_opensea_url(contract, chain)

        button_rows = []
        row1 = [
            InlineKeyboardButton(text="🔍 Explorer", url=explorer_link),
            InlineKeyboardButton(text="🌊 OpenSea", url=opensea_link),
        ]
        button_rows.append(row1)

        # Ethos / Creator Social Button
        if ethos_profile.get("ethos_url") and ethos_profile.get("score", 0) > 0:
            button_rows.append([InlineKeyboardButton(text="🛡️ Ethos Profile", url=ethos_profile["ethos_url"])])
        elif ethos_profile.get("x_handle"):
            button_rows.append([InlineKeyboardButton(text=f"🐦 @{ethos_profile['x_handle']}", url=f"https://x.com/{ethos_profile['x_handle']}")])

        if dex_info.get("url"):
            dex_label = f"📈 {dex_info.get('dex_id', 'DEX')} Chart"
            button_rows.append([InlineKeyboardButton(text=dex_label, url=dex_info["url"])])

        reply_markup = InlineKeyboardMarkup(button_rows)

        # ── Build Telegram Message ────────────────────────────────────
        header_name = f"<b>{escape_html(name)}</b> ({escape_html(symbol)})\n" if name else ""
        minter_info = f" | 👥 Minters: <b>{unique_minters}</b>" if unique_minters is not None else ""
        creator_info = f"\n👤 Creator: <code>{deployer_addr[:6]}...{deployer_addr[-4:]}</code>" if deployer_addr else ""
        ethos_line = format_telegram_ethos_badge(ethos_profile)
        dex_line = f"\n{dex_info['formatted_line']}" if dex_info.get("formatted_line") else ""

        text = (
            f"🆕 <b>New NFT Drop Detected!</b>\n\n"
            f"{header_name}"
            f"🔗 Chain: <b>{chain.capitalize()}</b>\n"
            f"📄 Contract: <code>{short_contract}</code>{creator_info}"
            f"{ethos_line}\n"
            f"🏷️ Standard: <b>{standard}</b>\n"
            f"🔥 Mints: <b>{mint_count}</b> ({age_hours}h old){minter_info}"
            f"{dex_line}\n\n"
            f"<b>AI Legitimacy Audit:</b>\n"
            f"{verdict_badge(ai_result)}"
        )

        # ── Send Image or Text Alert ──────────────────────────────────
        delivered = await deliver_alert(
            text=text, reply_markup=reply_markup, image_url=image_url,
            label=" Drops",
        )

        if delivered:
            print(f"[Drops] 🆕 Alerted: {short_contract} | {standard} | {mint_count} mints | {age_hours}h | AI {ai_result['score']}/100 on {chain}")


async def deliver_alert(text, reply_markup=None, image_url=None,
                        send_photo=None, send_text=None, label=""):
    """Send an alert and report truthfully whether Telegram accepted it.

    Returns True only when a send completed. Callers must not log "Alerted" unless
    this returns True: the previous code left the text fallback unguarded, so a
    Telegram rejection propagated while the operator saw an alert in the terminal
    that never arrived. That is the "shows in the terminal but never reaches TG"
    failure, and it is worse than a crash because it looks like success.

    Photo failures fall back to text. A text failure is reported, never swallowed
    silently.
    """
    photo_sender = send_photo or asend_photo
    text_sender = send_text or asend

    if image_url:
        try:
            img_bytes = await download_image_bytes(image_url)
            if img_bytes:
                await photo_sender(img_bytes, caption=text, parse_mode="HTML",
                                   reply_markup=reply_markup)
                return True
        except Exception as photo_err:
            print(f"[Alert]{label} Photo send failed: {photo_err} — falling back to text")

    try:
        await text_sender(text, reply_markup=reply_markup)
        return True
    except Exception as text_err:
        print(f"[Alert]{label} ❌ DELIVERY FAILED, alert did not reach Telegram: {text_err}")
        return False


async def check_drops():
    """Main scanning loop across all EVM chains using pure RPC & Ethos reputation."""
    global last_checked_blocks

    print(f"[Drops] 🔍 Scanning {len(ALL_SUPPORTED_CHAINS)} chains for fresh NFT drops...")
    semaphore = asyncio.Semaphore(5)

    for chain in ALL_SUPPORTED_CHAINS:
        try:
            current_block = await get_current_block(chain)
            if current_block is None:
                print(f"[Drops] ⚠️ {chain}: RPC unreachable (block fetch failed)")
                continue

            last_checked = last_checked_blocks[chain]
            step = EVM_CHAINS.get(chain, {}).get("block_step", 60)
            if last_checked is None:
                last_checked = max(0, current_block - step)
                last_checked_blocks[chain] = last_checked
                checkpoint.set_block(chain, last_checked)

            # No new blocks since last scan — skip so we never re-scan the same
            # window (a source of duplicate alerts).
            if current_block <= last_checked:
                continue

            from_block = last_checked + 1
            # Clamp the span so a long downtime doesn't produce an oversized
            # getLogs range that public RPCs reject. Skipping the excess is
            # preferable to a failed scan that never advances the watermark.
            max_span = max(step * 5, 2000)
            if current_block - from_block > max_span:
                skipped = (current_block - max_span) - from_block
                print(f"[Drops] ⚠️ {chain}: downtime gap too large, skipping "
                      f"{skipped} block(s) to keep getLogs within RPC limits")
                from_block = current_block - max_span

            transfers = await get_recent_transfers(chain, from_block, current_block)

            if transfers is None:
                # RPC failure — do NOT advance the watermark; retry this range
                # next cycle instead of skipping it.
                print(f"[Drops] ⚠️ {chain}: RPC error during getLogs (all endpoints failed)")
                continue

            if not transfers:
                # Empty range still counts as fully processed.
                last_checked_blocks[chain] = current_block
                checkpoint.set_block(chain, current_block)
                print(f"[Drops] {chain}: 0 mint events in blocks {from_block}→{current_block} ({current_block - from_block + 1} blocks)")
                continue

            contracts = {}
            for tx in transfers:
                contract = tx.get("rawContract", {}).get("address", "").lower()
                if not contract:
                    continue
                contracts.setdefault(contract, []).append(tx)

            if contracts:
                print(f"[Drops] Found mint activity on {len(contracts)} contract(s) on {chain}")

            # Sort by activity (number of mint events in batch) and take top 8 to stay fast
            sorted_contracts = sorted(contracts.items(), key=lambda x: len(x[1]), reverse=True)[:8]

            tasks = [
                evaluate_contract_drop(chain, contract, txs, semaphore)
                for contract, txs in sorted_contracts
            ]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

            # Commit the watermark only AFTER every alert from this range has been
            # dispatched. Advancing earlier means a crash mid-evaluation loses
            # those drops for good; advancing here means a crash re-scans the
            # range and the persisted dedup set suppresses the duplicates.
            last_checked_blocks[chain] = current_block
            checkpoint.set_block(chain, current_block)

        except Exception as e:
            print(f"[Drops Error] {chain}: {e}")

    # One durable write per full sweep rather than per chain. Wrapped so a
    # storage error here can never take down the scan loop; the next sweep
    # retries and the watermarks are still correct in memory.
    try:
        checkpoint.flush(force=True)
    except Exception as e:
        print(f"[Drops] ⚠️ Checkpoint flush failed: {e}")
