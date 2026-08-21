import json
import os
import requests
import threading

WATCHLIST_FILE = "watchlist.json"
_watchlist_cache = None
_lock = threading.Lock()

try:
    from private.config_live import OPENSEA_API_KEY
except ImportError:
    from config import OPENSEA_API_KEY

def load_watchlist():
    """Load watchlist from JSON file — persists across restarts."""
    global _watchlist_cache
    with _lock:
        if _watchlist_cache is not None:
            return _watchlist_cache

        if os.path.exists(WATCHLIST_FILE):
            try:
                with open(WATCHLIST_FILE, "r") as f:
                    _watchlist_cache = json.load(f)
                    return _watchlist_cache
            except Exception:
                pass
        
        _watchlist_cache = []
        return _watchlist_cache

def save_watchlist(watchlist):
    """Save watchlist to JSON file and update cache."""
    global _watchlist_cache
    with _lock:
        _watchlist_cache = watchlist
        with open(WATCHLIST_FILE, "w") as f:
            json.dump(watchlist, f, indent=2)

def lookup_contract(contract_address, chain="ethereum"):
    """
    Look up a contract on OpenSea and return collection details.
    Returns dict with name, slug, floor or None if not found.
    """
    headers = {"x-api-key": OPENSEA_API_KEY}

    # Step 1 — get collection slug from contract
    url = f"https://api.opensea.io/api/v2/chain/{chain}/contract/{contract_address}"
    res = requests.get(url, headers=headers, timeout=10)

    if res.status_code != 200:
        return None

    data = res.json()
    slug = data.get("collection", "")
    if not slug:
        return None

    # Step 2 — get collection details
    url2 = f"https://api.opensea.io/api/v2/collections/{slug}"
    res2 = requests.get(url2, headers=headers, timeout=10)

    if res2.status_code != 200:
        return None

    col = res2.json()
    name = col.get("name", slug)

    # Step 3 — get floor price
    url3 = f"https://api.opensea.io/api/v2/collections/{slug}/stats"
    res3 = requests.get(url3, headers=headers, timeout=10)
    floor = 0.0
    if res3.status_code == 200:
        stats = res3.json()
        floor = float(stats.get("total", {}).get("floor_price", 0.0))

    return {
        "name": name,
        "slug": slug,
        "chain": chain,
        "contract": contract_address.lower(),
        "floor_alert_low": round(floor * 0.8, 4) if floor and floor > 0 else 0.01,
        "floor_alert_high": round(floor * 1.5, 4) if floor and floor > 0 else 1.0,
        "current_floor": floor,
    }

def add_to_watchlist(contract_address, chain="ethereum", custom_low=None, custom_high=None):
    """
    Add a contract to the watchlist.
    Returns (success, message) tuple.
    """
    watchlist = load_watchlist()

    # Check if already watching
    contract_lower = contract_address.lower()
    for item in watchlist:
        if item["contract"] == contract_lower and item.get("chain", "ethereum") == chain:
            return False, f"Already watching {item['name']} on {chain}"

    # Look up on OpenSea
    col = lookup_contract(contract_address, chain)
    if not col:
        return False, f"Could not find collection on OpenSea for chain {chain}."

    if custom_low is not None:
        col["floor_alert_low"] = custom_low
    if custom_high is not None:
        col["floor_alert_high"] = custom_high

    watchlist.append(col)
    save_watchlist(watchlist)
    return True, col

def remove_from_watchlist(contract_address):
    """
    Remove a contract from the watchlist.
    Returns (success, message) tuple.
    """
    watchlist = load_watchlist()
    contract_lower = contract_address.lower()
    original_len = len(watchlist)
    watchlist = [w for w in watchlist if w["contract"] != contract_lower]

    if len(watchlist) == original_len:
        return False, "Contract not found in watchlist."

    save_watchlist(watchlist)
    return True, "Removed successfully."

def get_watchlist():
    """Return current watchlist."""
    return load_watchlist()

def merge_with_config(config_collections):
    """
    Merge static config collections with dynamic watchlist.
    Used by floor.py and mint.py to get full list.
    """
    watchlist = load_watchlist()
    watchlist_contracts = {w["contract"] for w in watchlist}

    # Add config collections not already in watchlist
    merged = list(watchlist)
    for col in config_collections:
        if col["contract"].lower() not in watchlist_contracts:
            merged.append(col)

    return merged
