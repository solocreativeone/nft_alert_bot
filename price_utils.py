"""
price_utils.py - Pricing, USD conversion, floor formatting, and address shortening utilities.
"""
import threading
import time
from typing import Optional
import requests

# ── Price Cache State ─────────────────────────────────────────────────────────
_cached_eth_usd_price: Optional[float] = None
_cached_price_time: float = 0.0
_price_lock = threading.Lock()

# Cache price for 5 minutes (300 seconds) to avoid redundant API calls
ETH_PRICE_CACHE_TTL = 300.0


def shorten_address(address: str) -> str:
    """Shorten an address for display: e.g. 0x34f4...06b5."""
    if not address or len(address) <= 10:
        return address or ""
    return f"{address[:6]}...{address[-4:]}"


def format_eth(val: float) -> str:
    """Format ETH amount cleanly without unnecessary trailing zeros or scientific notation."""
    if val is None:
        return ""
    # Format with up to 6 decimal places and strip excess trailing zeros
    s = f"{val:.6f}".rstrip("0").rstrip(".")
    return s if s else "0"


def format_usd(usd_val: float) -> str:
    """Format USD amount into clean representation.

    Examples:
      200.0 -> $200
      1.156 -> $1.16
      123.73 -> $123.73
      2474.62 -> $2,474.62
    """
    if usd_val is None:
        return ""
    if abs(usd_val - round(usd_val)) < 0.005:
        return f"${round(usd_val):,}"
    elif usd_val >= 0.01:
        return f"${usd_val:,.2f}"
    else:
        return f"${usd_val:,.4f}"


def _fetch_fresh_eth_usd_price() -> Optional[float]:
    """Fetch current ETH/USD spot price from public price APIs."""
    # 1. Try Coinbase spot price
    try:
        res = requests.get(
            "https://api.coinbase.com/v2/prices/ETH-USD/spot",
            timeout=5,
            headers={"User-Agent": "NFTAlertBot/1.0"},
        )
        if res.status_code == 200:
            data = res.json()
            return float(data["data"]["amount"])
    except Exception:
        pass

    # 2. Try Binance ticker price as fallback
    try:
        res = requests.get(
            "https://api.binance.com/api/v3/ticker/price?symbol=ETHUSDT",
            timeout=5,
            headers={"User-Agent": "NFTAlertBot/1.0"},
        )
        if res.status_code == 200:
            data = res.json()
            return float(data["price"])
    except Exception:
        pass

    return None


def get_eth_usd_price(max_age: float = ETH_PRICE_CACHE_TTL) -> Optional[float]:
    """Return cached ETH/USD price if fresh within max_age seconds, else fetch new.

    If fetching fresh price fails and cached price has expired, returns None.
    Never returns a stale value beyond max_age.
    """
    global _cached_eth_usd_price, _cached_price_time
    now = time.monotonic()
    with _price_lock:
        if _cached_eth_usd_price is not None and (now - _cached_price_time) < max_age:
            return _cached_eth_usd_price

    price = _fetch_fresh_eth_usd_price()
    with _price_lock:
        if price is not None and price > 0:
            _cached_eth_usd_price = price
            _cached_price_time = time.monotonic()
            return price
        return None


def set_cached_eth_usd_price(price: Optional[float], timestamp: Optional[float] = None):
    """Explicitly set cached price (primarily for testing)."""
    global _cached_eth_usd_price, _cached_price_time
    with _price_lock:
        _cached_eth_usd_price = price
        _cached_price_time = time.monotonic() if timestamp is None else timestamp


def clear_cached_price():
    """Clear cached price."""
    global _cached_eth_usd_price, _cached_price_time
    with _price_lock:
        _cached_eth_usd_price = None
        _cached_price_time = 0.0


def format_floor_display(
    floor: Optional[float] = None,
    eth_usd_price: Optional[float] = None,
    is_free_mint: bool = False,
    is_alert: bool = True,
) -> Optional[str]:
    """Format floor price string according to UX requirements.

    - Free mint: '🆓 Free Mint'
    - Alerts with USD: '💰 Floor: 0.05 ETH | $200'
    - Alerts without USD: '💰 Floor: 0.05 ETH'
    - List with USD: 'Floor: 0.05 ETH | $200'
    - List without USD: 'Floor: 0.05 ETH'
    - Missing / 0 floor when not free mint: returns None (do not invent floor)
    """
    if is_free_mint:
        return "🆓 Free Mint"

    if floor is None or floor <= 0:
        return None

    eth_str = f"{format_eth(floor)} ETH"
    prefix = "💰 Floor: " if is_alert else "Floor: "

    if eth_usd_price is not None and eth_usd_price > 0:
        usd_val = floor * eth_usd_price
        usd_str = format_usd(usd_val)
        return f"{prefix}{eth_str} | {usd_str}"
    else:
        return f"{prefix}{eth_str}"
