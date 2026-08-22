"""
ethos.py - Ethos Network integration for creator reputation & credibility scoring.

Fetches on-chain credibility scores, community vouches, linked social identities
(X/Twitter, Farcaster, Discord, Telegram), and fraud/slash flags for deployer addresses.
"""
import asyncio
import requests
from notifier import escape_html

ETHOS_BASE_URL = "https://api.ethos.network"
ETHOS_CLIENT_HEADER = "NFTAlertBot@2.0"

# API endpoints. IMPORTANT: the old v1 endpoint this module used to call,
# /api/v1/users/address:<addr>, is DEAD and returns 404 ("Cannot GET"). Because
# the old code only acted on status_code == 200 and swallowed everything else,
# that failure was silent: score fell through to the v2 score endpoint but
# vouch_count, is_flagged, socials and profile_id were permanently blank.
# /api/v2/user/by/address/<addr> is the live replacement and carries all of it.
ETHOS_USER_URL = ETHOS_BASE_URL + "/api/v2/user/by/address/{addr}"
ETHOS_SCORE_URL = ETHOS_BASE_URL + "/api/v2/score/address?address={addr}"

# Bound the cache so a long-running process can't grow it without limit.
MAX_CACHED_PROFILES = 5000

# In-memory cache for deployer address reputation
_ethos_cache = {}


def _extract_socials(user: dict):
    """Pull linked social handles out of a v2 user record.

    userkeys look like "service:x.com:2259434528" (the numeric platform id) or
    "address:0x...". The human-readable handle lives in `username`, so a
    service userkey tells us WHICH platform the username belongs to.
    """
    x_handle = farcaster_handle = discord_id = telegram_id = None
    username = user.get("username")

    for key in user.get("userkeys") or []:
        k = str(key).lower()
        if not k.startswith("service:"):
            continue
        ident = k.rsplit(":", 1)[-1]
        if "x.com" in k or "twitter" in k:
            x_handle = (username or ident).lstrip("@")
        elif "farcaster" in k:
            farcaster_handle = username or ident
        elif "discord" in k:
            discord_id = username or ident
        elif "telegram" in k:
            telegram_id = username or ident

    return x_handle, farcaster_handle, discord_id, telegram_id


def _is_flagged(reviews: dict, user: dict) -> bool:
    """Decide whether a creator carries genuinely negative attestations.

    The dead v1 code flagged on `negativeReviewCount > 0`. Reproducing that
    literally would be far too aggressive now that the data actually arrives:
    this flag HARD-BLOCKS an alert in drops.py, and a single negative review
    among hundreds of positives is normal for an established creator. So we
    flag only when negatives actually outweigh positives.
    """
    if user.get("isSlashed"):
        return True
    negative = reviews.get("negative") or 0
    positive = reviews.get("positive") or 0
    return negative > 0 and negative > positive


def get_ethos_profile(wallet_address: str) -> dict:
    """
    Query Ethos Network API for a wallet address.
    Returns structured reputation dict:
    {
        "score": int,           # Credibility score (e.g. 1420)
        "tier": str,            # "High Trust" | "Established" | "Unranked" | "Flagged"
        "profile_id": int/str,
        "x_handle": str,        # Twitter/X username if linked
        "farcaster_handle": str,# Farcaster username/id if linked
        "discord_id": str,
        "telegram_id": str,
        "vouch_count": int,
        "is_flagged": bool,     # True if negative reviews outweigh positive, or slashed
        "ethos_url": str,       # Direct link to Ethos profile
        "summary": str,         # One-line summary for Telegram/Gemini
    }
    """
    if not wallet_address:
        return _empty_profile(wallet_address)

    addr = wallet_address.lower()
    if addr in _ethos_cache:
        return _ethos_cache[addr]

    headers = {
        "X-Ethos-Client": ETHOS_CLIENT_HEADER,
        "accept": "application/json",
    }

    score = 0
    profile_id = None
    x_handle = None
    farcaster_handle = None
    discord_id = None
    telegram_id = None
    vouch_count = 0
    is_flagged = False
    ethos_url = None

    # Attempt 1: full user record (score + socials + vouches + reviews).
    # A 404 here simply means the wallet has no Ethos profile yet.
    try:
        res = requests.get(ETHOS_USER_URL.format(addr=addr), headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, dict):
                score = data.get("score") or 0
                profile_id = data.get("profileId") or data.get("id")

                stats = data.get("stats") or {}
                vouch_count = (
                    ((stats.get("vouch") or {}).get("received") or {}).get("count") or 0
                )
                reviews = ((stats.get("review") or {}).get("received") or {})
                is_flagged = _is_flagged(reviews, data)

                x_handle, farcaster_handle, discord_id, telegram_id = _extract_socials(data)

                links = data.get("links")
                if isinstance(links, dict):
                    ethos_url = links.get("profile")
        elif res.status_code != 404:
            print(f"[Ethos] user lookup returned HTTP {res.status_code} for {addr[:10]}...")
    except Exception as e:
        print(f"[Ethos] user lookup failed for {addr[:10]}...: {e}")

    # Attempt 2: score-only endpoint. Works for any address (returns 0 for
    # unknown wallets), so it also covers the no-profile case above.
    if score == 0:
        try:
            res2 = requests.get(ETHOS_SCORE_URL.format(addr=addr), headers=headers, timeout=5)
            if res2.status_code == 200:
                data2 = res2.json()
                if isinstance(data2, dict):
                    score = data2.get("score") or 0
        except Exception as e:
            print(f"[Ethos] score lookup failed for {addr[:10]}...: {e}")

    # Determine reputation tier
    if is_flagged or score < 0:
        tier = "Flagged"
    elif score >= 1200:
        tier = "High Trust"
    elif score >= 500:
        tier = "Established"
    elif score > 0:
        tier = "Active"
    else:
        tier = "Unranked"

    if not ethos_url:
        ethos_url = (
            f"https://app.ethos.network/profile/{profile_id}"
            if profile_id
            else f"https://app.ethos.network/profile/address:{addr}"
        )

    profile = {
        "address": addr,
        "score": int(score),
        "tier": tier,
        "profile_id": profile_id,
        "x_handle": x_handle,
        "farcaster_handle": farcaster_handle,
        "discord_id": discord_id,
        "telegram_id": telegram_id,
        "vouch_count": vouch_count,
        "is_flagged": is_flagged,
        "ethos_url": ethos_url,
    }

    # Generate summary line
    profile["summary"] = format_ethos_summary(profile)
    if len(_ethos_cache) >= MAX_CACHED_PROFILES:
        _ethos_cache.clear()
    _ethos_cache[addr] = profile
    return profile


def _empty_profile(address: str) -> dict:
    return {
        "address": address.lower() if address else "",
        "score": 0,
        "tier": "Unranked",
        "profile_id": None,
        "x_handle": None,
        "farcaster_handle": None,
        "discord_id": None,
        "telegram_id": None,
        "vouch_count": 0,
        "is_flagged": False,
        "ethos_url": f"https://app.ethos.network/profile/address:{address.lower() if address else ''}",
        "summary": "Unranked / New Wallet",
    }


def format_ethos_summary(profile: dict) -> str:
    """Format a clean, readable one-liner for Telegram or AI prompt."""
    if not profile:
        return "Unranked / New Wallet"

    tier = profile.get("tier", "Unranked")
    score = profile.get("score", 0)
    x = profile.get("x_handle")
    fc = profile.get("farcaster_handle")
    flagged = profile.get("is_flagged", False)

    if flagged:
        return "🚨 Flagged / Negative Community Attestations"

    parts = []
    if score > 0:
        parts.append(f"Score: {score} ({tier})")
    else:
        parts.append(f"{tier}")

    if x:
        parts.append(f"X: @{x}")
    elif fc:
        parts.append(f"FC: @{fc}")

    vouch = profile.get("vouch_count", 0)
    if vouch > 0:
        parts.append(f"{vouch} vouches")

    return " | ".join(parts)


def format_telegram_ethos_badge(profile: dict) -> str:
    """Format an HTML section for Telegram alert with creator reputation & social links."""
    if not profile or (profile.get("score", 0) == 0 and not profile.get("x_handle") and not profile.get("is_flagged")):
        return ""

    score = profile.get("score", 0)
    tier = profile.get("tier", "Unranked")
    x = profile.get("x_handle")
    flagged = profile.get("is_flagged", False)

    if flagged:
        return "\n🛡️ Ethos Rep: 🚨 <b>Warning: Flagged Creator / Slashed</b>"

    social_text = f" (@{escape_html(x)})" if x else ""
    return f"\n🛡️ Ethos Rep: <b>{tier}</b> (Score: <b>{score}</b>){social_text}"


async def get_ethos_profile_async(wallet_address: str) -> dict:
    """Asynchronously fetch Ethos profile in worker thread."""
    return await asyncio.to_thread(get_ethos_profile, wallet_address)
