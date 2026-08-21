"""
deployer_cache.py - Cache and risk evaluator for contract deployer wallets.

Tracks creator wallets and their history (total deployed, rugs, average score).
Fast-blocks contracts deployed by known serial ruggers before calling Gemini.
"""
import os
import json
import asyncio
from datetime import datetime, timezone
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


async def get_contract_creation_info(chain: str, contract_address: str, custom_rpc_chains: dict = None) -> dict:
    """
    Query a Blockscout / Etherscan-compatible explorer for a contract's creation
    record via the getcontractcreation endpoint.

    Returns whatever the explorer provides so callers can derive the contract's
    TRUE deployment age (not the age of the earliest mint in a scan window):

        {
            "creator":      lowercase deployer address (str, "" if unknown),
            "tx_hash":      creation transaction hash (str, "" if unknown),
            "deploy_block": int block number, or None,
            "deploy_ts":    int unix timestamp, or None,
        }
    """
    empty = {"creator": "", "tx_hash": "", "deploy_block": None, "deploy_ts": None}
    if not contract_address:
        return empty

    explorer_base = ""
    if custom_rpc_chains and chain in custom_rpc_chains:
        explorer_base = custom_rpc_chains[chain].get("explorer", "")
    elif chain == "robinhood":
        explorer_base = "https://robinhoodchain.blockscout.com"

    if not explorer_base:
        return empty

    api_url = f"{explorer_base}/api?module=contract&action=getcontractcreation&contractaddresses={contract_address}"

    try:
        def _fetch():
            return requests.get(api_url, timeout=6)

        res = await asyncio.to_thread(_fetch)
        if res.status_code == 200:
            data = res.json()
            if data.get("status") == "1" and data.get("result"):
                items = data["result"]
                if isinstance(items, list) and len(items) > 0:
                    item = items[0]
                    creator = item.get("contractCreator") or item.get("creatorAddress") or ""
                    tx_hash = (
                        item.get("txHash")
                        or item.get("creationTxHash")
                        or item.get("transactionHash")
                        or ""
                    )
                    return {
                        "creator": creator.lower() if creator else "",
                        "tx_hash": tx_hash.lower() if tx_hash else "",
                        "deploy_block": _to_int(item.get("blockNumber")),
                        "deploy_ts": _to_int(item.get("timestamp") or item.get("blockTimestamp")),
                    }
    except Exception as e:
        print(f"[DeployerCache] ⚠️ Creation-info lookup failed for {contract_address[:10]}...: {e}")

    return empty


async def get_contract_creator(chain: str, contract_address: str, custom_rpc_chains: dict = None) -> str:
    """
    Return the contract creator/deployer address (lowercase), or "" if unknown.

    Thin backward-compatible wrapper over get_contract_creation_info() for callers
    that only need the deployer address.
    """
    info = await get_contract_creation_info(chain, contract_address, custom_rpc_chains)
    return info.get("creator", "")
