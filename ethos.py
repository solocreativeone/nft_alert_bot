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

# In-memory LRU/dict cache for deployer address reputation
_ethos_cache = {}


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
        "is_flagged": bool,     # True if negative review/slashed
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

    # Attempt 1: Fetch user profile by address
    try:
        url = f"{ETHOS_BASE_URL}/api/v1/users/address:{addr}"
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, dict):
                score = data.get("score") or data.get("credibilityScore") or 0
                profile_id = data.get("id") or data.get("profileId")
                vouch_count = data.get("vouchCount") or len(data.get("vouches", []))
                
                # Check negative reviews / slashes
                negative_count = data.get("negativeReviewCount", 0)
                if negative_count > 0 or data.get("isSlashed", False):
                    is_flagged = True

                # Parse linked socials
                links = data.get("links") or data.get("socialLinks") or []
                if isinstance(links, list):
                    for link in links:
                        service = str(link.get("service", "")).lower()
                        val = link.get("value") or link.get("username") or link.get("id") or ""
                        if "x.com" in service or "twitter" in service:
                            x_handle = val.lstrip("@")
                        elif "farcaster" in service:
                            farcaster_handle = val
                        elif "discord" in service:
                            discord_id = val
                        elif "telegram" in service:
                            telegram_id = val
    except Exception as e:
        # Best effort; gracefully fallback
        pass

    # Attempt 2: If score not found, try score endpoint
    if score == 0:
        try:
            score_url = f"{ETHOS_BASE_URL}/api/v2/score/address?address={addr}"
            res2 = requests.get(score_url, headers=headers, timeout=5)
            if res2.status_code == 200:
                data2 = res2.json()
                if isinstance(data2, dict):
                    score = data2.get("score") or data2.get("credibilityScore") or 0
        except Exception:
            pass

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

    ethos_url = f"https://app.ethos.network/profile/{profile_id}" if profile_id else f"https://app.ethos.network/profile/address:{addr}"

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
