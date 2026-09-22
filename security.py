"""Provider abstraction for contract inspection.

Only fields documented by the providers are normalized here. Missing provider
fields remain unknown rather than being guessed.
"""
import requests

from watchlist import normalize_contract

HONEYPOT_SUPPORTED = {"ethereum": 1, "bsc": 56, "base": 8453}
CHAIN_IDS = {"ethereum": 1, "polygon": 137, "base": 8453, "arbitrum": 42161,
             "optimism": 10, "bsc": 56, "avalanche": 43114, "robinhood": 4663, "arc": 5042}


def resolve_chain(chain):
    name = (chain or "ethereum").strip().lower()
    # Load the scanner's chain registry lazily so command tests and provider
    # utilities do not initialize unrelated scanner/notifier dependencies.
    try:
        from drops import EVM_CHAINS
    except ImportError:
        EVM_CHAINS = {chain_name: {} for chain_name in CHAIN_IDS}
    if name not in EVM_CHAINS:
        raise ValueError(f"Unsupported chain. Supported chains: {', '.join(EVM_CHAINS)}")
    chain_id = EVM_CHAINS[name].get("chain_id") or EVM_CHAINS[name].get("chainId")
    if chain_id is None:
        chain_id = CHAIN_IDS.get(name)
    return name, int(chain_id) if chain_id is not None else None


def _headers(api_key, bearer=False):
    if not api_key:
        return {}
    return {"Authorization": f"Bearer {api_key}"} if bearer else {"X-API-KEY": api_key}


class HoneypotProvider:
    name = "Honeypot.is"

    def __init__(self, api_key="", http_get=requests.get):
        self.api_key = api_key
        self.http_get = http_get

    def inspect(self, contract, chain, chain_id):
        response = self.http_get(
            "https://api.honeypot.is/v2/IsHoneypot",
            params={"address": contract, "chainID": chain_id},
            headers=_headers(self.api_key), timeout=15,
        )
        response.raise_for_status()
        return response.json()


class GoPlusProvider:
    name = "GoPlus"

    def __init__(self, api_key="", http_get=requests.get):
        self.api_key = api_key
        self.http_get = http_get

    def inspect(self, contract, chain, chain_id):
        response = self.http_get(
            f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}",
            params={"contract_addresses": contract},
            headers=_headers(self.api_key, bearer=True), timeout=15,
        )
        response.raise_for_status()
        return response.json()


def _goplus_result(raw, contract):
    result = raw.get("result", raw) if isinstance(raw, dict) else {}
    if isinstance(result, dict):
        return result.get(contract) or result.get(contract.lower()) or result.get(contract.upper()) or result
    return {}


def _risk_from_honeypot(data):
    summary = data.get("summary") or {}
    hp = data.get("honeypotResult") or {}
    risk = summary.get("risk")
    flags = summary.get("flags")
    if not isinstance(flags, list):
        flags = data.get("flags") if isinstance(data.get("flags"), list) else []
    flag_names = [item.get("flag") if isinstance(item, dict) else item for item in flags]
    if hp.get("isHoneypot") is True or risk in {"honeypot", "very_high", "high"}:
        status = "high_risk"
    elif risk in {"medium"} or flag_names:
        status = "suspicious"
    elif risk in {"low", "very_low"}:
        status = "looks_legit"
    else:
        status = "not_assessed"
    return status, flag_names


def _risk_from_goplus(data):
    risky = {"is_honeypot", "malicious_address", "honeypot_related_address", "is_blacklisted", "transfer_pausable"}
    flags = [key for key in risky if str(data.get(key)) == "1"]
    if any(key in flags for key in ("is_honeypot", "malicious_address", "honeypot_related_address")):
        return "high_risk", flags
    if flags or str(data.get("is_open_source")) == "0":
        return "suspicious", flags + (["closed_source"] if str(data.get("is_open_source")) == "0" else [])
    if data and str(data.get("is_open_source")) == "1":
        return "looks_legit", flags
    return "not_assessed", flags


def normalize_result(contract, chain, provider_name, raw):
    contract = normalize_contract(contract)
    details = {"provider": provider_name}
    if provider_name == "Honeypot.is":
        token = raw.get("token") or {}
        summary = raw.get("summary") or {}
        code = raw.get("contractCode") or {}
        simulation = raw.get("simulationResult") or {}
        risk, flags = _risk_from_honeypot(raw)
        details.update({
            "token": {key: token[key] for key in ("name", "symbol", "decimals", "totalHolders") if key in token},
            "verified": code.get("openSource"),
            "proxy": code.get("isProxy"),
            "honeypot": (raw.get("honeypotResult") or {}).get("isHoneypot"),
            "simulation_success": raw.get("simulationSuccess"),
            "risk_level": summary.get("riskLevel"),
            "taxes": {key: simulation[key] for key in ("buyTax", "sellTax", "transferTax") if key in simulation},
        })
    else:
        data = _goplus_result(raw, contract)
        risk, flags = _risk_from_goplus(data)
        details.update({key: data[key] for key in (
            "token_name", "token_symbol", "is_open_source", "is_proxy", "is_honeypot",
            "is_mintable", "is_blacklisted", "transfer_pausable", "dex",
        ) if key in data})
    return {"contract": contract, "chain": chain, "risk": risk, "flags": flags, "details": details}


def inspect_contract(contract, chain="ethereum", honeypot_key="", goplus_key="", http_get=requests.get):
    chain, chain_id = resolve_chain(chain)
    contract = normalize_contract(contract)
    if not (len(contract) == 42 and contract.startswith("0x") and all(c in "0123456789abcdef" for c in contract[2:])):
        raise ValueError("Invalid contract address")
    if chain in HONEYPOT_SUPPORTED:
        provider = HoneypotProvider(honeypot_key, http_get=http_get)
    else:
        provider = GoPlusProvider(goplus_key, http_get=http_get)
    raw = provider.inspect(contract, chain, chain_id)
    return normalize_result(contract, chain, provider.name, raw)
