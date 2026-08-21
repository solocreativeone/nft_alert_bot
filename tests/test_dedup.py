import dedup


def _clear():
    dedup._alerted = set()
    dedup._last_reset = None


def test_mark_and_check():
    _clear()
    assert not dedup.already_alerted("link-1")
    dedup.mark_alerted("link-1")
    assert dedup.already_alerted("link-1")


def test_reset_clears_store():
    _clear()
    dedup.mark_alerted("link-2")
    assert dedup.already_alerted("link-2")
    # Force a reset by pretending we last reset on an unknown day.
    dedup._last_reset = None
    dedup.reset_if_new_day()
    assert not dedup.already_alerted("link-2")
