"""Cooperative dedup for NFTCalendar drops.

Both the calendar (6 hr) and live_drops (10 min) jobs scrape NFTCalendar for
upcoming ETH mints. They share this store — keyed on the full drop link — so
whichever job sees a drop first alerts on it and the other skips.

The store is a bounded set (newest MAX_ALERTED keys) backed by checkpoint.py, so
it survives a restart: previously it was in-memory only and every restart
re-alerted every drop still on the scraped page. It intentionally does NOT reset
daily either; the old daily wipe made the bot re-alert the same drop once per UTC
day. ``reset_if_new_day()`` is kept as an importable no-op for the existing
callers (live_drops.py, calendar_tracker.py).

Both jobs run on the same scheduler thread, so no locking is needed.
"""
from collections import deque

import checkpoint

MAX_ALERTED = 5000
SECTION = "calendar_links"

_alerted = set()
_alerted_order = deque(maxlen=MAX_ALERTED)


def reset_if_new_day():
    """No-op — kept for backward compatibility with existing callers.

    Daily resets were removed because they caused the bot to re-alert the same
    drop every UTC day. The store now persists across restarts.
    """
    return


def already_alerted(key):
    """Return True if this drop link has already been alerted, ever."""
    return key in _alerted or checkpoint.was_seen(SECTION, key)


def mark_alerted(key):
    """Record that this drop link has been alerted, evicting the oldest if full."""
    if key in _alerted:
        checkpoint.mark_seen(SECTION, key)
        return
    if len(_alerted_order) == MAX_ALERTED:
        oldest = _alerted_order.popleft()
        _alerted.discard(oldest)
    _alerted.add(key)
    _alerted_order.append(key)
    checkpoint.mark_seen(SECTION, key)
