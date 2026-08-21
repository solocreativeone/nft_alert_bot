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
    from private.config_live import GEMINI_API_KEY, GEMINI_MIN_SCORE
    print("[Gemini] ✅ Private config loaded")
except ImportError:
    from config import GEMINI_API_KEY, GEMINI_MIN_SCORE

# ── Client & Rate Limiter ─────────────────────────────────────────────────────

_client = None
_last_gemini_call_time = 0.0
_gemini_lock = asyncio.Lock()

# ── Daily-quota circuit breaker ───────────────────────────────────────────────
# The free tier is capped at 500 requests per DAY, not just 15 per minute. Once a
# quota 429 is hit, further calls only 429 again and waste latency — so we stop
# calling until a cooldown expires, then probe once (hourly for the daily cap,
# ~1 min for a transient per-minute limit).
_gemini_cooldown_until = 0.0
_daily_call_count = 0
_daily_count_date = None


def _reset_daily_counter_if_new_day():
    global _daily_call_count, _daily_count_date
    today = datetime.now(timezone.utc).date()
    if _daily_count_date != today:
        _daily_call_count = 0
        _daily_count_date = today


def _compute_cooldown_seconds(err_str: str) -> float:
    """Decide how long to pause calls after a 429, honoring retryDelay if given."""
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


def _trip_cooldown(err_str: str) -> float:
    global _gemini_cooldown_until
    cooldown = _compute_cooldown_seconds(err_str)
    _gemini_cooldown_until = time.time() + cooldown
    return cooldown


def get_client():
    """Lazy-init the Gemini client on first use."""
    global _client
    if _client is None:
        if not GEMINI_AVAILABLE:
            return None
        if not GEMINI_API_KEY:
            print("[Gemini] ⚠️ GEMINI_API_KEY not set - filter disabled")
            return None
        _client = genai.Client(api_key=GEMINI_API_KEY)
        print("[Gemini] ✅ gemini-3.5-flash-lite ready")
    return _client

async def _rate_limited_generate(client, prompt: str):
    """Ensure Gemini free-tier rate limit (15 RPM -> 4.0s interval) is respected."""
    global _last_gemini_call_time, _daily_call_count
    async with _gemini_lock:
        now = time.time()
        elapsed = now - _last_gemini_call_time
        if elapsed < 4.0:
            await asyncio.sleep(4.0 - elapsed)
        _last_gemini_call_time = time.time()

        _reset_daily_counter_if_new_day()
        _daily_call_count += 1

        def _call():
            return client.models.generate_content(
                model="gemini-3.5-flash-lite",
                contents=prompt,
            )

        return await asyncio.to_thread(_call)

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
        return {
            "score": 50,
            "verdict": "UNKNOWN",
            "reason": "Gemini filter disabled or unconfigured."
        }

    # ── Circuit breaker: skip the call entirely while quota is exhausted ──
    # Returns a blocking verdict (RATE_LIMITED) so we stay quiet instead of
    # flooding alerts with unscored drops once the daily cap is hit.
    if time.time() < _gemini_cooldown_until:
        return {
            "score": 0,
            "verdict": "RATE_LIMITED",
            "reason": "AI audit paused — Gemini daily quota reached.",
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

    try:
        response = await _rate_limited_generate(client, prompt)
        raw = response.text.strip()

        # Strip accidental markdown fences
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        result = json.loads(raw)
        _score_cache[contract] = result
        print(f"[Gemini] 🧠 {clean_payload['collection_name']} ({contract[:10]}...) -> {result['verdict']} ({result['score']}/100)")
        return result

    except Exception as e:
        err_str = str(e)
        if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
            cooldown = _trip_cooldown(err_str)
            print(f"[Gemini] ⛔ Quota hit after {_daily_call_count} call(s) today — "
                  f"pausing AI audits for ~{round(cooldown / 60)} min")
            return {
                "score": 0,
                "verdict": "RATE_LIMITED",
                "reason": "AI audit paused — Gemini quota reached.",
            }
        print(f"[Gemini] ⚠️ Scoring failed for {contract[:10]}...: {err_str[:100]}")
        return {
            "score": 0,
            "verdict": "ERROR",
            "reason": "AI audit failed — alert suppressed to avoid unvetted drops.",
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
