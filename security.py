"""Provider abstraction for contract inspection.

Only fields documented by the providers are normalized here. Missing provider
fields remain unknown rather than being guessed.
"""
import requests
import hashlib
import time

from watchlist import normalize_contract

HONEYPOT_SUPPORTED = {"ethereum": 1, "bsc": 56, "base": 8453}
GOPLUS_SUPPORTED = {"ethereum": 1, "polygon": 137, "base": 8453, "arbitrum": 42161,
                    "optimism": 10, "bsc": 56, "avalanche": 43114}
CHAIN_IDS = {"ethereum": 1, "polygon": 137, "base": 8453, "arbitrum": 42161,
             "optimism": 10, "bsc": 56, "avalanche": 43114, "robinhood": 4663, "arc": 5042}
SECURITY_SUPPORTED_CHAINS = set(HONEYPOT_SUPPORTED) | set(GOPLUS_SUPPORTED)


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
        status_code = getattr(response, "status_code", getattr(response, "status", 200))
        if status_code == 400:
            try:
                body = response.json()
                if "invalid chain" in str(body.get("error", "")).lower():
                    return {"unsupported_chain": True, "raw": body}
            except Exception:
                pass
        if status_code == 404:
            return {"empty_response": True, "raw": {}}
        response.raise_for_status()
        return response.json()


class GoPlusProvider:
    name = "GoPlus"

    def __init__(self, api_key="", app_key="", app_secret="", http_get=requests.get, http_post=requests.post):
        self.api_key = api_key
        self.app_key = app_key
        self.app_secret = app_secret
        self.http_get = http_get
        self.http_post = http_post

    def _access_token(self):
        if not self.app_key or not self.app_secret:
            return self.api_key or ""
        timestamp = str(int(time.time()))
        signature = hashlib.sha1((self.app_key + timestamp + self.app_secret).encode()).hexdigest()
        response = self.http_post(
            "https://api.gopluslabs.io/api/v1/token",
            json={"app_key": self.app_key, "sign": signature, "time": int(timestamp)},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        result = payload.get("result") if isinstance(payload, dict) else None
        token = result if isinstance(result, str) else None
        if isinstance(result, dict):
            token = result.get("access_token") or result.get("token")
        token = token or (payload.get("access_token") if isinstance(payload, dict) else None)
        if not token:
            raise RuntimeError("GoPlus token response did not contain an access token")
        # GoPlus may return the scheme together with the token. The request
        # layer adds the Bearer scheme, so avoid sending "Bearer Bearer ...".
        return token[7:].strip() if token.lower().startswith("bearer ") else token

    def inspect(self, contract, chain, chain_id):
        token = self._access_token()
        response = self.http_get(
            f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}",
            params={"contract_addresses": contract},
            headers=_headers(token, bearer=True), timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            code = payload.get("code")
            if code == 2022 or (isinstance(payload.get("message"), str) and "not supported" in payload.get("message").lower()):
                return {"unsupported_chain": True, "raw": payload}
            if code not in (1, None) and not payload.get("result"):
                raise RuntimeError(f"GoPlus error: {payload.get('message') or code}")
        return payload


def _goplus_result(raw, contract):
    result = raw.get("result", raw) if isinstance(raw, dict) else {}
    if isinstance(result, dict):
        return result.get(contract) or result.get(contract.lower()) or result.get(contract.upper()) or {}
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
    if isinstance(raw, dict) and raw.get("unsupported_chain"):
        return {
            "contract": contract,
            "chain": chain,
            "risk": "not_assessed",
            "flags": [],
            "details": {"provider": provider_name, "unsupported_chain": True},
            "unsupported_chain": True,
        }
    details = {"provider": provider_name}
    if provider_name == "Honeypot.is":
        if isinstance(raw, dict) and raw.get("empty_response"):
            return {
                "contract": contract,
                "chain": chain,
                "risk": "not_assessed",
                "flags": [],
                "details": {"provider": provider_name, "empty_response": True},
                "empty_response": True,
            }
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
        if not data:
            return {
                "contract": contract,
                "chain": chain,
                "risk": "not_assessed",
                "flags": [],
                "details": {"provider": provider_name, "empty_response": True},
                "empty_response": True,
            }
        risk, flags = _risk_from_goplus(data)
        details.update({key: data[key] for key in (
            "token_name", "token_symbol", "is_open_source", "is_proxy", "is_honeypot",
            "is_mintable", "is_blacklisted", "transfer_pausable", "dex",
        ) if key in data})
        token_info = {}
        if "token_name" in data:
            token_info["name"] = data["token_name"]
        if "token_symbol" in data:
            token_info["symbol"] = data["token_symbol"]
        if token_info:
            details["token"] = token_info
        if "is_open_source" in data:
            val = str(data["is_open_source"])
            details["verified"] = True if val == "1" else (False if val == "0" else None)
        if "is_proxy" in data:
            val = str(data["is_proxy"])
            details["proxy"] = True if val == "1" else (False if val == "0" else None)
        if "is_honeypot" in data:
            val = str(data["is_honeypot"])
            details["honeypot"] = True if val == "1" else (False if val == "0" else None)
    return {"contract": contract, "chain": chain, "risk": risk, "flags": flags, "details": details}


def inspect_contract(contract, chain="ethereum", honeypot_key="", goplus_key="", app_key="", app_secret="", http_get=requests.get, http_post=requests.post):
    chain, chain_id = resolve_chain(chain)
    contract = normalize_contract(contract)
    if not (len(contract) == 42 and contract.startswith("0x") and all(c in "0123456789abcdef" for c in contract[2:])):
        raise ValueError("Invalid contract address")
    if chain not in SECURITY_SUPPORTED_CHAINS:
        return {
            "contract": contract,
            "chain": chain,
            "risk": "not_assessed",
            "flags": [],
            "details": {"unsupported_chain": True},
            "unsupported_chain": True,
        }
    if chain in HONEYPOT_SUPPORTED:
        provider = HoneypotProvider(honeypot_key, http_get=http_get)
    else:
        provider = GoPlusProvider(goplus_key, app_key, app_secret, http_get=http_get, http_post=http_post)
    raw = provider.inspect(contract, chain, chain_id)
    return normalize_result(contract, chain, provider.name, raw)
