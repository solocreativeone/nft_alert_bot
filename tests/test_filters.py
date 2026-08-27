from datetime import datetime, timezone, timedelta

from notifier import escape_html
from live_drops import is_junk, is_within_age


def test_escape_html_escapes_specials():
    assert escape_html("a & b < c > d") == "a &amp; b &lt; c &gt; d"


def test_escape_html_handles_empty():
    assert escape_html("") == ""
    assert escape_html(None) == ""


def test_is_junk_flags_bad_names():
    assert is_junk("test collection", "x")
    assert is_junk("SCAM token", "x")
    assert is_junk("0x1234567890abcdef", "x")  # unnamed contract
    assert is_junk("ab", "x")                  # too short
    assert is_junk("", "x")                    # empty


def test_is_junk_allows_real_names():
    assert not is_junk("Bored Ape Yacht Club", "bayc")


def test_is_within_age():
    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(hours=100)).isoformat()
    assert is_within_age(recent, max_hours=72)
    assert not is_within_age(old, max_hours=72)
    assert not is_within_age("", max_hours=72)
    assert not is_within_age("not-a-date", max_hours=72)
