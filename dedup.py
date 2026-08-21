"""Cooperative dedup for NFTCalendar drops.

Both the calendar (6 hr) and live_drops (10 min) jobs scrape NFTCalendar for
upcoming ETH mints. They share this store — keyed on the full drop link — so
whichever job sees a drop first alerts on it and the other skips.

The store is a bounded in-memory set (newest MAX_ALERTED keys). It intentionally
does NOT reset daily: the old daily wipe made the bot re-alert the same drop once
per UTC day. ``reset_if_new_day()`` is kept as an importable no-op for the
existing callers (live_drops.py, calendar_tracker.py).

Both jobs run on the same scheduler thread, so a plain in-memory set is safe with
no locking. NOTE: state is in-memory only — a process restart clears it.
"""
from collections import deque

MAX_ALERTED = 5000

_alerted = set()
_alerted_order = deque(maxlen=MAX_ALERTED)


def reset_if_new_day():
    """No-op — kept for backward compatibility with existing callers.

    Daily resets were removed because they caused the bot to re-alert the same
    drop every UTC day. The store now persists for the life of the process.
    """
    return


def already_alerted(key):
    """Return True if this drop link has already been alerted."""
    return key in _alerted


def mark_alerted(key):
    """Record that this drop link has been alerted, evicting the oldest if full."""
    if key in _alerted:
        return
    if len(_alerted_order) == MAX_ALERTED:
        oldest = _alerted_order.popleft()
        _alerted.discard(oldest)
    _alerted.add(key)
    _alerted_order.append(key)
