"""
dex_liquidity.py - Real-time DEX liquidity and trading pool detector.

Queries DexScreener's public API to detect if an NFT/token has associated
liquidity pairs (e.g. Uniswap, Sushiswap, PancakeSwap), pool depth, and 24h volume.
"""
import asyncio
import requests


def _format_currency(amount: float) -> str:
    """Format USD amount into clean human-readable representation."""
    if amount is None:
        return "$0"
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M"
    if amount >= 1_000:
        return f"${amount / 1_000:.1f}K"
    return f"${amount:,.0f}"


def fetch_dex_screener_sync(contract_address: str) -> dict:
    """Synchronous worker to query DexScreener token endpoint."""
    if not contract_address:
        return {}

    url = f"https://api.dexscreener.com/latest/dex/tokens/{contract_address}"
    try:
        res = requests.get(url, timeout=5, headers={"User-Agent": "NFTAlertBot/1.0"})
        if res.status_code == 200:
            return res.json()
    except Exception as e:
        print(f"[DexLiquidity] ⚠️ DexScreener lookup failed for {contract_address[:10]}...: {e}")
    return {}


def parse_dex_data(raw_data: dict, chain: str = "") -> dict:
    """
    Parse DexScreener API payload and extract the best/highest liquidity pair.

    ``chain`` filters pairs to the chain the drop was detected on. DexScreener's
    /tokens/<address> endpoint returns pairs for the SAME address across EVERY
    chain it knows, and an address can exist on several chains with unrelated
    tokens behind it. Without the filter the highest-liquidity pair anywhere wins,
    so an Ethereum drop can report PulseChain liquidity (confirmed live: USDC's
    Ethereum address returns 29 PulseChain pairs at $10.7M vs 1 Ethereum pair at
    $884K). Passing an empty chain keeps the old cross-chain behaviour.
    """
    default_result = {
        "has_liquidity": False,
        "liquidity_usd": 0.0,
        "volume_24h": 0.0,
        "dex_id": "",
        "pair_address": "",
        "base_token_name": "",
        "base_token_symbol": "",
        "quote_token_symbol": "",
        "price_usd": "",
        "url": "",
        "formatted_line": "",
    }

    if not raw_data or not isinstance(raw_data, dict):
        return default_result

    pairs = raw_data.get("pairs")
    if not pairs or not isinstance(pairs, list):
        return default_result

    # Keep only pairs on the chain we're actually looking at. DexScreener's
    # chainId slugs match this bot's EVM_CHAINS keys for every supported chain
    # (verified live: ethereum, base, polygon, arbitrum, optimism, bsc, avalanche).
    if chain:
        want = chain.strip().lower()
        same_chain = [
            p
            for p in pairs
            if isinstance(p, dict) and str(p.get("chainId", "")).lower() == want
        ]
        # If the chain isn't on DexScreener at all (e.g. zora, robinhood), report
        # no liquidity rather than silently quoting a different chain's pool.
        pairs = same_chain

    if not pairs:
        return default_result

    # Sort pairs by highest liquidity USD
    def get_liq(p):
        try:
            return float(p.get("liquidity", {}).get("usd", 0) or 0)
        except (ValueError, TypeError):
            return 0.0

    valid_pairs = sorted(pairs, key=get_liq, reverse=True)
    if not valid_pairs:
        return default_result

    best_pair = valid_pairs[0]
    liq_usd = get_liq(best_pair)
    
    vol_24h = 0.0
    try:
        vol_24h = float(best_pair.get("volume", {}).get("h24", 0) or 0)
    except (ValueError, TypeError):
        pass

    dex_id = best_pair.get("dexId", "").capitalize() or "DEX"
    pair_address = best_pair.get("pairAddress", "")
    quote_sym = best_pair.get("quoteToken", {}).get("symbol", "")
    base_sym = best_pair.get("baseToken", {}).get("symbol", "")
    base_name = best_pair.get("baseToken", {}).get("name", "")
    price_usd = best_pair.get("priceUsd", "")
    url = best_pair.get("url", "")

    has_liq = liq_usd > 0
    formatted_line = ""
    if has_liq:
        formatted_liq = _format_currency(liq_usd)
        formatted_vol = _format_currency(vol_24h)
        formatted_line = f"💧 Liquidity: <b>{formatted_liq}</b> ({dex_id}) | 24h Vol: <b>{formatted_vol}</b>"

    return {
        "has_liquidity": has_liq,
        "liquidity_usd": liq_usd,
        "volume_24h": vol_24h,
        "dex_id": dex_id,
        "pair_address": pair_address,
        "base_token_name": base_name,
        "base_token_symbol": base_sym,
        "quote_token_symbol": quote_sym,
        "price_usd": price_usd,
        "url": url,
        "formatted_line": formatted_line,
    }


async def get_dex_liquidity(chain: str, contract_address: str) -> dict:
    """
    Asynchronously query DEX liquidity and pool activity for a token/NFT address.

    Results are restricted to ``chain`` so a drop never reports another chain's
    liquidity pool.
    """
    raw_data = await asyncio.to_thread(fetch_dex_screener_sync, contract_address)
    return parse_dex_data(raw_data, chain)
