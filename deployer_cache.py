"""
deployer_cache.py - Cache and risk evaluator for contract deployer wallets.

Tracks creator wallets and their history (total deployed, rugs, average score).
Fast-blocks contracts deployed by known serial ruggers before calling Gemini.
"""
import os
import json
import asyncio
from datetime import datetime, timezone
from typing import Optional
import requests

DEPLOYERS_FILE = os.path.join(os.path.dirname(__file__), "deployers.json")


def load_deployers(filepath: str = None) -> dict:
    """Load deployers from JSON file."""
    path = filepath or DEPLOYERS_FILE
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[DeployerCache] ⚠️ Error loading {path}: {e}")
        return {}


def save_deployers(data: dict, filepath: str = None) -> bool:
    """Persist deployers data to JSON file atomically."""
    path = filepath or DEPLOYERS_FILE
    tmp_path = f"{path}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
        return True
    except Exception as e:
        print(f"[DeployerCache] ⚠️ Error saving {path}: {e}")
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        return False


def get_deployer_stats(deployer_address: str, filepath: str = None) -> dict | None:
    """Get stored statistics for a deployer address."""
    if not deployer_address:
        return None
    data = load_deployers(filepath)
    return data.get(deployer_address.lower())


def is_known_serial_rugger(deployer_address: str, filepath: str = None) -> tuple[bool, str]:
    """
    Check if a deployer wallet is a known serial rugger or bad actor.

    Returns:
        (is_rugger: bool, reason: str)
    """
    if not deployer_address:
        return False, ""

    stats = get_deployer_stats(deployer_address, filepath)
    if not stats:
        return False, ""

    rugs = stats.get("rugs", 0)
    total = stats.get("total_deployed", 0)
    last_verdict = stats.get("last_verdict", "")
    avg_score = stats.get("avg_score", 50.0)

    if rugs > 0:
        return True, f"Deployer previously launched {rugs} suspected rug(s) out of {total} contract(s)."

    if last_verdict == "LIKELY_RUG":
        return True, f"Deployer's most recent contract was flagged as LIKELY_RUG (avg score: {avg_score:.0f}/100)."

    if total >= 2 and avg_score < 30:
        return True, f"Deployer has a persistently low trust score ({avg_score:.0f}/100 across {total} contracts)."

    return False, ""


def record_deployer_result(
    deployer_address: str,
    contract_address: str,
    score: int,
    verdict: str,
    filepath: str = None
) -> dict:
    """
    Update deployer wallet statistics after a contract is scored.
    """
    if not deployer_address:
        return {}

    addr = deployer_address.lower()
    c_addr = contract_address.lower() if contract_address else ""
    data = load_deployers(filepath)

    stats = data.get(addr, {
        "total_deployed": 0,
        "rugs": 0,
        "last_verdict": "",
        "avg_score": 0.0,
        "contracts": [],
    })

    old_total = stats.get("total_deployed", 0)
    old_avg = stats.get("avg_score", 0.0)
    new_total = old_total + 1
    new_avg = round(((old_avg * old_total) + score) / new_total, 1)

    rugs = stats.get("rugs", 0)
    if verdict == "LIKELY_RUG":
        rugs += 1

    contracts = stats.get("contracts", [])
    if c_addr and c_addr not in contracts:
        contracts.append(c_addr)

    stats["total_deployed"] = new_total
    stats["rugs"] = rugs
    stats["last_verdict"] = verdict
    stats["avg_score"] = new_avg
    stats["contracts"] = contracts[-50:]  # keep recent 50
    stats["last_updated"] = datetime.now(timezone.utc).isoformat()

    data[addr] = stats
    save_deployers(data, filepath)
    return stats


def _to_int(value):
    """Parse an int from a decimal or 0x-hex string; return None on failure."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        s = str(value).strip()
        if not s:
            return None
        if s.lower().startswith("0x"):
            return int(s, 16)
        return int(s)
    except (ValueError, TypeError):
        return None


# ── RPC-based contract creation lookup ────────────────────────────────────────
# The previous implementation queried Etherscan/Blockscout getcontractcreation and
# never worked. Probed live: the code built `https://etherscan.io/api?...`, which
# answers {"status":"0","message":"NOTOK","result":"Invalid API URL endpoint, use
# api.etherscan.io"}; with the host corrected it answers "You are using a deprecated
# V1 endpoint, switch to Etherscan V2"; and Zora's Blockscout returns 404 for the
# same path. So creator was always "" and deploy_ts always None, which is why Gemini
# reported "unranked deployer" on every drop and Ethos had no address to look up.
#
# Resolving creation over the RPC endpoints the bot already uses needs no API keys,
# no per-chain hostnames and no V2 migration. Verified against Etherscan ground
# truth for BAYC: deploy block 12287507, creator 0xaba7161a7fb69c88e16ed9f455ce62b791ee4d03.

RPC_TIMEOUT = 8
# Bounded search window. The bot only alerts on contracts newer than
# MAX_CONTRACT_AGE_HOURS (48h, roughly 14,400 ethereum blocks and more on faster
# chains), and drops.py already knows the block of the first mint it observed.
# Searching further back would spend calls establishing a fact that cannot change
# the outcome, since an older contract is skipped by the age gate anyway.
DEFAULT_MAX_LOOKBACK = 120_000


def _rpc_call(rpcs, method, params, timeout=RPC_TIMEOUT):
    """POST a JSON-RPC call, trying each endpoint until one answers.

    Returns the result, or None when no endpoint could answer. None means unknown
    and must never be coerced into a value: a wrong creator is worse than no
    creator because it feeds a reputation lookup and an AI verdict.
    """
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    for url in rpcs or []:
        try:
            res = requests.post(url, json=payload, timeout=timeout)
            if res.status_code != 200:
                continue
            body = res.json()
            if "error" in body:
                continue
            if "result" in body:
                return body["result"]
        except Exception:
            continue
    return None


def _get_code_at_block(chain, address, block, rpcs=None):
    """eth_getCode at a historical block. Returns the hex string, or None."""
    tag = "latest" if block is None else hex(block)
    return _rpc_call(rpcs, "eth_getCode", [address, tag])


def _get_block_with_txs(chain, block_number, rpcs=None):
    return _rpc_call(rpcs, "eth_getBlockByNumber", [hex(block_number), True],
                     timeout=RPC_TIMEOUT * 2)


def _get_receipt(chain, tx_hash, rpcs=None):
    return _rpc_call(rpcs, "eth_getTransactionReceipt", [tx_hash])


def _has_code(code) -> bool:
    return bool(code) and code not in ("0x", "0x0")


def find_creation_block(chain, contract, known_block, max_lookback=None,
                        rpcs=None):
    """Binary search for the first block where the contract has code.

    `known_block` is a block where the contract is known to exist (drops.py passes
    the block of the first mint it saw). Returns the creation block, or None when
    it cannot be established inside the window.

    Historical eth_getCode needs archive state. Most public endpoints serve it, but
    a pruned node can answer misleadingly, so the lower bound is verified rather
    than assumed: if code already exists at the bottom of the window the contract
    predates it and None is returned instead of a wrong block.
    """
    if known_block is None:
        return None
    lookback = DEFAULT_MAX_LOOKBACK if max_lookback is None else max_lookback

    if not _has_code(_get_code_at_block(chain, contract, known_block, rpcs=rpcs)):
        # No code even where the caller saw activity: bad input or an RPC that
        # cannot answer. Either way we know nothing.
        return None

    low = max(0, known_block - lookback)
    if _has_code(_get_code_at_block(chain, contract, low, rpcs=rpcs)):
        # Already deployed before the window opened, so it is older than the age
        # gate cares about.
        return None

    high = known_block
    while low < high:
        mid = (low + high) // 2
        code = _get_code_at_block(chain, contract, mid, rpcs=rpcs)
        if code is None:
            return None
        if _has_code(code):
            high = mid
        else:
            low = mid + 1
    return low


def extract_creation_from_block(chain, contract, block_number, rpcs=None):
    """Pull creator, tx hash and timestamp out of the creation block.

    Only transactions with to=None can create a contract, and the creating one is
    identified by its receipt naming this contract address. The timestamp is
    returned even when the creator cannot be matched, because the age gate only
    needs the timestamp and a partial result still beats nothing.
    """
    empty = {"creator": "", "tx_hash": "", "deploy_block": None, "deploy_ts": None}
    block = _get_block_with_txs(chain, block_number, rpcs=rpcs)
    if not block:
        return empty

    result = {
        "creator": "",
        "tx_hash": "",
        "deploy_block": block_number,
        "deploy_ts": _to_int(block.get("timestamp")),
    }

    target = (contract or "").lower()
    for tx in block.get("transactions", []):
        if tx.get("to") is not None:
            continue
        receipt = _get_receipt(chain, tx.get("hash"), rpcs=rpcs)
        if not receipt:
            continue
        created = (receipt.get("contractAddress") or "").lower()
        if created and created == target:
            result["creator"] = (tx.get("from") or "").lower()
            result["tx_hash"] = (tx.get("hash") or "").lower()
            break

    return result


async def get_contract_creation_info(chain: str, contract_address: str,
                                     custom_rpc_chains: Optional[dict] = None,
                                     known_block: Optional[int] = None) -> dict:
    """
    Resolve a contract's creation record over RPC.

    Returns the data callers need to derive the contract's TRUE deployment age
    (not the age of the earliest mint in a scan window):

        {
            "creator":      lowercase deployer address (str, "" if unknown),
            "tx_hash":      creation transaction hash (str, "" if unknown),
            "deploy_block": int block number, or None,
            "deploy_ts":    int unix timestamp, or None,
        }

    `known_block` is a block where the contract is known to exist. When omitted the
    current head is used, which still works but searches a wider window.
    """
    empty = {"creator": "", "tx_hash": "", "deploy_block": None, "deploy_ts": None}
    if not contract_address:
        return empty

    chain_cfg = (custom_rpc_chains or {}).get(chain) or {}
    rpcs = chain_cfg.get("rpcs") or []
    if not rpcs:
        return empty

    def _resolve():
        anchor = known_block
        if anchor is None:
            head = _rpc_call(rpcs, "eth_blockNumber", [])
            anchor = _to_int(head)
            if anchor is None:
                return empty

        block_number = find_creation_block(
            chain=chain, contract=contract_address, known_block=anchor,
            rpcs=rpcs)
        if block_number is None:
            return empty
        return extract_creation_from_block(
            chain=chain, contract=contract_address, block_number=block_number,
            rpcs=rpcs)

    try:
        return await asyncio.to_thread(_resolve)
    except Exception as e:
        print(f"[DeployerCache] ⚠️ Creation lookup failed for {contract_address[:10]}...: {e}")
        return empty


async def get_contract_creator(chain: str, contract_address: str,
                               custom_rpc_chains: Optional[dict] = None,
                               known_block: Optional[int] = None) -> str:
    """
    Return the contract creator/deployer address (lowercase), or "" if unknown.

    Thin backward-compatible wrapper over get_contract_creation_info() for callers
    that only need the deployer address.
    """
    info = await get_contract_creation_info(
        chain, contract_address, custom_rpc_chains, known_block=known_block)
    return info.get("creator", "")
