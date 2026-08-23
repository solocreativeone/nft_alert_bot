import checkpoint
import dedup


def _clear():
    """Reset both the in-memory store and its persistent backing.

    already_alerted() consults the checkpoint store as well, so clearing only the
    in-memory set would leave keys from a previous test visible.
    """
    dedup._alerted = set()
    dedup._alerted_order = dedup.deque(maxlen=dedup.MAX_ALERTED)
    checkpoint._state = None
    checkpoint._seen_order = {}
    checkpoint._dirty = False


def test_mark_and_check():
    _clear()
    assert not dedup.already_alerted("link-1")
    dedup.mark_alerted("link-1")
    assert dedup.already_alerted("link-1")


def test_mark_is_idempotent():
    _clear()
    dedup.mark_alerted("link-dup")
    dedup.mark_alerted("link-dup")
    assert dedup.already_alerted("link-dup")
    assert len(dedup._alerted_order) == 1


def test_reset_does_not_forget():
    """reset_if_new_day() is now a no-op: a day rollover must NOT wipe the store.

    The old daily wipe caused the bot to re-alert the same drop once per UTC day.
    """
    _clear()
    dedup.mark_alerted("link-2")
    assert dedup.already_alerted("link-2")
    dedup.reset_if_new_day()
    assert dedup.already_alerted("link-2")


def test_bounded_eviction():
    """Oldest keys are evicted once the store fills, newest are retained."""
    _clear()
    total = dedup.MAX_ALERTED + 10
    for i in range(total):
        dedup.mark_alerted(f"key-{i}")

    # Store never grows past its cap.
    assert len(dedup._alerted) == dedup.MAX_ALERTED
    assert len(dedup._alerted_order) == dedup.MAX_ALERTED

    # The first 10 keys were pushed out; the most recent survive.
    assert not dedup.already_alerted("key-0")
    assert not dedup.already_alerted("key-9")
    assert dedup.already_alerted("key-10")
    assert dedup.already_alerted(f"key-{total - 1}")
