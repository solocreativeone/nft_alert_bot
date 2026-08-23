"""
gemini_filter.py - AI-powered NFT legitimacy scorer and contract researcher.

Uses gemini-3.5-flash-lite (google-genai SDK) to evaluate on-chain metrics,
collection name/symbol, token metadata, and verified source code.
Returns a structured score (0-100), verdict, and a concise analyst summary.
"""
import asyncio
import json
import re
import time
from datetime import datetime, timezone

try:
    from google import genai
    from google.genai import types
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    print("[Gemini] ⚠️ google-genai not installed - filter disabled")

try:
    from private.config_live import GEMINI_API_KEY, GEMINI_API_KEYS, GEMINI_MIN_SCORE, GEMINI_DAILY_LIMIT
    print("[Gemini] ✅ Private config loaded")
except ImportError:
    from config import GEMINI_API_KEY, GEMINI_API_KEYS, GEMINI_MIN_SCORE, GEMINI_DAILY_LIMIT

import checkpoint

# ── Key pool ──────────────────────────────────────────────────────────────────
# Gemini's free tier caps each key at GEMINI_DAILY_LIMIT requests per DAY (plus a
# ~15 RPM burst limit). One key alone silences the AI audit for the rest of the
# day once it runs dry, and because is_worth_alerting() fails CLOSED that also
# silences every alert. So we hold a pool and rotate to the next usable key
# whenever the current one hits its quota.
#
# Per-key state (daily count + cooldown expiry) is persisted via checkpoint.py
# keyed on a SHA-256 fingerprint, never the key itself, so a restart does not
# reset counters and start hammering a key that is already exhausted.

def _build_key_pool():
    """Ordered, de-duplicated list of configured keys (GEMINI_API_KEY first)."""
    pool = []
    for candidate in [GEMINI_API_KEY] + list(GEMINI_API_KEYS or []):
        if not candidate:
            continue
        cleaned = str(candidate).strip()
        if cleaned and cleaned not in pool:
            pool.append(cleaned)
    return pool


_key_pool = _build_key_pool()
_clients: dict = {}          # api_key -> genai.Client
_active_index = 0
_last_gemini_call_time = 0.0
_gemini_lock = asyncio.Lock()
_pool_logged = False


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _key_state(api_key: str) -> dict:
    """Persisted state for one key, with the daily counter rolled over if stale."""
    state = checkpoint.get_gemini_key_state(api_key)
    if state.get("date") != _today():
        state = {"date": _today(), "count": 0, "cooldown_until": state.get("cooldown_until", 0.0)}
    return state


def _save_key_state(api_key: str, state: dict, flush_now: bool = False):
    checkpoint.set_gemini_key_state(
        api_key,
        date=state.get("date") or _today(),
        count=int(state.get("count", 0)),
        cooldown_until=float(state.get("cooldown_until", 0.0)),
        flush_now=flush_now,
    )


def _key_is_available(api_key: str, now: float | None = None) -> bool:
    """A key is usable when it is off cooldown and under its daily cap."""
    now = time.time() if now is None else now
    state = _key_state(api_key)
    if now < float(state.get("cooldown_until", 0.0)):
        return False
    if GEMINI_DAILY_LIMIT and int(state.get("count", 0)) >= GEMINI_DAILY_LIMIT:
        return False
    return True


def _label(api_key: str) -> str:
    """Short human-readable id for logs. Never prints the key itself."""
    if not _key_pool:
        return "none"
    try:
        return f"key #{_key_pool.index(api_key) + 1}/{len(_key_pool)}"
    except ValueError:
        return "key ?"


def select_key(now: float | None = None):
    """Return the next usable key, rotating past exhausted ones.

    Starts at the currently active key so we stay on it while it still has
    quota, then walks the pool in order. Returns None when every key is spent,
    which trips the RATE_LIMITED verdict.
    """
    global _active_index
    if not _key_pool:
        return None
    now = time.time() if now is None else now
    for offset in range(len(_key_pool)):
        idx = (_active_index + offset) % len(_key_pool)
        candidate = _key_pool[idx]
        if _key_is_available(candidate, now):
            if idx != _active_index:
                print(f"[Gemini] 🔄 Rotating to {_label(candidate)} "
                      f"(previous key exhausted or cooling down)")
                _active_index = idx
            return candidate
    return None


def _compute_cooldown_seconds(err_str: str) -> float:
    """Decide how long to pause a key after a 429, honoring retryDelay if given."""
    retry = 0.0
    m = re.search(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+)", err_str)
    if m:
        retry = float(m.group(1))
    is_daily = "PerDay" in err_str or "per day" in err_str.lower()
    if is_daily:
        # Daily quota won't reset for hours — probe at most once an hour.
        return max(retry, 3600.0)
    # Transient per-minute limit — honor retryDelay, floor at 60s.
    return max(retry, 60.0)


def _trip_cooldown(err_str: str, api_key: str | None = None) -> float:
    """Park ONE key on cooldown. Other keys in the pool stay usable."""
    cooldown = _compute_cooldown_seconds(err_str)
    target = api_key if api_key is not None else (_key_pool[_active_index] if _key_pool else None)
    if target:
        state = _key_state(target)
        state["cooldown_until"] = time.time() + cooldown
        # A daily-quota 429 means this key is done for the day regardless of
        # what our local counter says (it may have been used elsewhere).
        if "PerDay" in err_str or "per day" in err_str.lower():
            state["count"] = max(int(state.get("count", 0)), GEMINI_DAILY_LIMIT or 0)
        _save_key_state(target, state, flush_now=True)
    return cooldown


def get_client(api_key: str | None = None):
    """Return a client for the given key (or the next usable one), caching per key."""
    global _pool_logged
    if not GEMINI_AVAILABLE:
        return None
    if not _key_pool:
        print("[Gemini] ⚠️ No Gemini API key configured - filter disabled")
        return None

    if not _pool_logged:
        print(f"[Gemini] ✅ gemini-3.5-flash-lite ready ({len(_key_pool)} key(s) in pool, "
              f"{GEMINI_DAILY_LIMIT}/day each)")
        _pool_logged = True

    key = api_key or select_key()
    if not key:
        return None
    if key not in _clients:
        _clients[key] = genai.Client(api_key=key)
    return _clients[key]


async def _rate_limited_generate(client, prompt: str, api_key: str | None = None):
    """Respect the free-tier burst limit (15 RPM -> 4.0s interval) and count usage."""
    global _last_gemini_call_time
    async with _gemini_lock:
        now = time.time()
        elapsed = now - _last_gemini_call_time
        if elapsed < 4.0:
            await asyncio.sleep(4.0 - elapsed)
        _last_gemini_call_time = time.time()

        if api_key:
            state = _key_state(api_key)
            state["count"] = int(state.get("count", 0)) + 1
            _save_key_state(api_key, state)

        def _call():
            return client.models.generate_content(
                model="gemini-3.5-flash-lite",
                contents=prompt,
            )

        return await asyncio.to_thread(_call)


def pool_status() -> dict:
    """Snapshot of every key's quota usage. Fingerprints only, never raw keys."""
    now = time.time()
    keys = []
    for idx, key in enumerate(_key_pool):
        state = _key_state(key)
        keys.append({
            "index": idx + 1,
            "fingerprint": checkpoint.fingerprint(key),
            "used_today": int(state.get("count", 0)),
            "limit": GEMINI_DAILY_LIMIT,
            "cooling_down": now < float(state.get("cooldown_until", 0.0)),
            "available": _key_is_available(key, now),
            "active": idx == _active_index,
        })
    return {
        "total_keys": len(_key_pool),
        "available_keys": sum(1 for k in keys if k["available"]),
        "keys": keys,
    }

# ── Score cache ───────────────────────────────────────────────────────────────

_score_cache: dict = {}   # contract_address -> result

# ── Prompt ────────────────────────────────────────────────────────────────────

_PROMPT = """\
You are an expert on-chain NFT researcher and smart contract risk auditor.
Analyze the provided NFT contract data (metrics, collection name/symbol, metadata sample, contract source, deployer history, and DEX liquidity if available) and provide an objective risk & legitimacy assessment.

Evaluation Criteria:
1. Mint Distribution: Penalize low unique minters relative to total mints (e.g. 5,000 mints from 2 wallets is a bot/self-mint). Reward organic wallet diversity.
2. Velocity: Extreme velocity (>1000 mints/hour on unknown contracts) is suspicious. Steady mints (5-150/hr) indicate genuine interest.
3. Collection Identity: Flag copycats (e.g. fake "Bored Ape", "CryptoPunk", generic "TEST" names). Look for authentic original names/symbols.
4. Metadata & Art: Check if token metadata or image URI is available, decentralized (IPFS/Arweave), or missing/broken.
5. Contract Source: If verified source is provided, check for honeypots, hidden mint fees, malicious transfer restrictions, or safe standard OpenZeppelin implementations.
6. Deployer Reputation & History: Consider deployer wallet past launches, previous rug counts, and average historical scores if present.
7. Ethos Network Credibility & Social Standing: Consider the creator's Ethos credibility score, linked verified Twitter/Farcaster identities, community vouches, or negative slash flags. High Ethos score (>1000) or verified handles strongly indicate a legitimate creator.
8. DEX Liquidity & Trading Activity: Check if an active DEX liquidity pool exists (e.g., Uniswap pair with backed liquidity). Genuine liquidity backing strongly signals a real project; $0 liquidity or pump-and-dump profiles raise risk.

Return ONLY a JSON object with this exact schema (no markdown, no backticks):
{
  "score": <int between 0 and 100>,
  "verdict": "LEGIT" | "SUSPICIOUS" | "LIKELY_RUG",
  "reason": "<1-2 clear, punchy sentences explaining the verdict and key risk/legitimacy factors>"
}
"""

# ── Public API ─────────────────────────────────────────────────────────────────

async def gemini_score_nft(contract_data: dict) -> dict:
    """
    Score an NFT contract using Gemini AI with full context.

    Args:
        contract_data: dict with keys:
            contract, chain, name, symbol, mint_count, age_hours,
            standard, unique_minters, mint_velocity_per_hour,
            token_uri, metadata, verified_source_snippet,
            deployer_address, deployer_stats, ethos_profile, dex_liquidity

    Returns:
        dict with keys: score (int), verdict (str), reason (str)
    """
    contract = contract_data.get("contract", "")

    # Return cached result - never score the same contract twice
    if contract in _score_cache:
        return _score_cache[contract]

    client = get_client()
    if client is None:
        # No key configured at all vs. every key exhausted are different states:
        # the former means the filter is intentionally off (UNKNOWN passes), the
        # latter means we could not vet this drop (RATE_LIMITED blocks).
        if _key_pool and select_key() is None:
            return {
                "score": 0,
                "verdict": "RATE_LIMITED",
                "reason": "AI audit paused — all Gemini keys have reached their daily quota.",
            }
        return {
            "score": 50,
            "verdict": "UNKNOWN",
            "reason": "Gemini filter disabled or unconfigured."
        }

    # Rotate to whichever key still has quota. None means the whole pool is spent,
    # so we return a blocking verdict instead of flooding unscored drops.
    active_key = select_key()
    if active_key is None:
        return {
            "score": 0,
            "verdict": "RATE_LIMITED",
            "reason": "AI audit paused — all Gemini keys have reached their daily quota.",
        }
    client = get_client(active_key)
    if client is None:
        return {
            "score": 0,
            "verdict": "RATE_LIMITED",
            "reason": "AI audit paused — no usable Gemini key.",
        }

    # Prepare cleaned payload to stay compact and informative
    clean_payload = {
        "contract_address": contract,
        "chain": contract_data.get("chain"),
        "collection_name": contract_data.get("name") or "Unknown",
        "symbol": contract_data.get("symbol") or "Unknown",
        "standard": contract_data.get("standard", "ERC-721"),
        "total_mints_detected": contract_data.get("mint_count", 0),
        "unique_minter_wallets": contract_data.get("unique_minters"),
        "contract_age_hours": contract_data.get("age_hours", 0),
        "mint_velocity_per_hour": contract_data.get("mint_velocity_per_hour", 0),
        "token_uri_sample": contract_data.get("token_uri"),
        "metadata_sample": contract_data.get("metadata"),
        "verified_contract_source": contract_data.get("verified_source_snippet"),
        "deployer_address": contract_data.get("deployer_address"),
        "deployer_history": contract_data.get("deployer_stats"),
        "ethos_creator_reputation": contract_data.get("ethos_profile", {}).get("summary") if isinstance(contract_data.get("ethos_profile"), dict) else contract_data.get("ethos_profile"),
        "dex_liquidity_info": contract_data.get("dex_liquidity"),
    }

    prompt = f"{_PROMPT}\n\nContract Data:\n{json.dumps(clean_payload, indent=2)}"

    # Try the active key, then rotate through any remaining keys on a quota 429.
    # A per-minute 429 on key A can often be served immediately by key B, so a
    # rotation retry keeps the audit alive instead of dropping the contract.
    attempts = max(1, len(_key_pool))
    last_err = ""
    for attempt in range(attempts):
        try:
            response = await _rate_limited_generate(client, prompt, api_key=active_key)
            raw = (response.text or "").strip()
            if not raw:
                # A safety block or MAX_TOKENS finish leaves .text empty/None.
                raise ValueError("empty response from Gemini")

            # Strip accidental markdown fences
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]

            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError(f"expected a JSON object, got {type(parsed).__name__}")

            # Normalize: a response can legitimately omit or mistype a field. Don't
            # discard an otherwise-usable verdict over a missing key -- previously any
            # KeyError here fell through to ERROR and suppressed the alert entirely.
            verdict = str(parsed.get("verdict") or "UNKNOWN").upper()
            try:
                score = int(float(parsed.get("score", 0) or 0))
            except (TypeError, ValueError):
                score = 0
            result = {
                "score": max(0, min(100, score)),
                "verdict": verdict,
                "reason": str(parsed.get("reason") or "").strip(),
            }

            _score_cache[contract] = result
            print(f"[Gemini] 🧠 {clean_payload['collection_name']} ({contract[:10]}...) -> {result['verdict']} ({result['score']}/100)")
            return result

        except Exception as e:
            err_str = str(e)
            last_err = err_str
            is_quota = "429" in err_str or "RESOURCE_EXHAUSTED" in err_str
            if not is_quota:
                print(f"[Gemini] ⚠️ Scoring failed for {contract[:10]}...: {err_str[:100]}")
                return {
                    "score": 0,
                    "verdict": "ERROR",
                    "reason": "AI audit failed — alert suppressed to avoid unvetted drops.",
                }

            # Quota hit: park this key, then try the next one that still has room.
            cooldown = _trip_cooldown(err_str, api_key=active_key)
            used = _key_state(active_key).get("count", 0)
            print(f"[Gemini] ⛔ Quota hit on {_label(active_key)} after {used} call(s) today — "
                  f"cooling it down for ~{round(cooldown / 60)} min")

            next_key = select_key()
            if next_key is None or next_key == active_key or attempt == attempts - 1:
                print("[Gemini] ⛔ All Gemini keys exhausted — pausing AI audits")
                return {
                    "score": 0,
                    "verdict": "RATE_LIMITED",
                    "reason": "AI audit paused — all Gemini keys have reached their quota.",
                }
            active_key = next_key
            client = get_client(active_key)
            if client is None:
                return {
                    "score": 0,
                    "verdict": "RATE_LIMITED",
                    "reason": "AI audit paused — no usable Gemini key.",
                }

    print(f"[Gemini] ⚠️ Scoring exhausted all keys for {contract[:10]}...: {last_err[:100]}")
    return {
        "score": 0,
        "verdict": "RATE_LIMITED",
        "reason": "AI audit paused — all Gemini keys have reached their quota.",
    }


def is_worth_alerting(result: dict, min_score: int = GEMINI_MIN_SCORE) -> bool:
    """
    Return True if this NFT meets quality criteria for a Telegram alert.

    Fail CLOSED on anything that means "we couldn't actually vet this":
    LIKELY_RUG, RATE_LIMITED (quota exhausted) and ERROR (API failure) are all
    blocked, so a Gemini outage suppresses alerts instead of flooding unvetted
    drops. UNKNOWN (filter intentionally disabled / not configured) still passes
    so users without a Gemini key keep getting alerts.
    """
    verdict = result.get("verdict", "UNKNOWN")
    if verdict in ("LIKELY_RUG", "RATE_LIMITED", "ERROR"):
        return False
    if verdict == "UNKNOWN":
        return True
    return result.get("score", 0) >= min_score


def verdict_badge(result: dict) -> str:
    """Return an HTML badge with score and analyst reason for Telegram."""
    v = result.get("verdict", "UNKNOWN")
    s = result.get("score", 50)
    reason = result.get("reason", "")
    
    if v == "LIKELY_RUG":
        badge = f"🚨 <b>Likely Rug / Bot Churn</b> ({s}/100)"
    elif v == "SUSPICIOUS":
        badge = f"⚠️ <b>Suspicious / High Risk</b> ({s}/100)"
    elif v == "LEGIT":
        badge = f"✅ <b>Looks Legit</b> ({s}/100)"
    elif v == "RATE_LIMITED":
        badge = f"⏳ <b>AI Audit Paused (quota)</b>"
    elif v == "ERROR":
        badge = f"⚠️ <b>AI Audit Failed</b>"
    else:
        badge = f"🤷 <b>Unscored</b>"

    if reason:
        return f"{badge}\n<i>💡 {reason}</i>"
    return badge
