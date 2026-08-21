"""Cooperative dedup for NFTCalendar drops.

Both the calendar (6 hr) and live_drops (10 min) jobs scrape NFTCalendar for
upcoming ETH mints. They share this store — keyed on the full drop link — so
whichever job sees a drop first alerts on it and the other skips. The set
resets daily so genuinely-recurring listings can re-surface once per day.

Both jobs run on the same scheduler thread, so a plain in-memory set is safe
with no locking.
"""
from datetime import datetime, timezone

_alerted = set()
_last_reset = None


def reset_if_new_day():
    """Clear the store when the UTC date rolls over."""
    global _alerted, _last_reset
    today = datetime.now(timezone.utc).date()
    if _last_reset != today:
        _alerted = set()
        _last_reset = today
        print("[Dedup] Daily reset — cleared NFTCalendar cache")


def already_alerted(key):
    """Return True if this drop link has already been alerted today."""
    return key in _alerted


def mark_alerted(key):
    """Record that this drop link has been alerted."""
    _alerted.add(key)
