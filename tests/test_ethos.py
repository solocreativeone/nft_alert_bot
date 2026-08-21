import pytest
from ethos import get_ethos_profile, format_ethos_summary, format_telegram_ethos_badge, _empty_profile

def test_empty_profile():
    res = _empty_profile("")
    assert res["score"] == 0
    assert res["tier"] == "Unranked"
    assert res["x_handle"] is None

def test_format_ethos_summary():
    profile = {
        "score": 1420,
        "tier": "High Trust",
        "x_handle": "alice",
        "vouch_count": 15,
        "is_flagged": False,
    }
    summary = format_ethos_summary(profile)
    assert "Score: 1420 (High Trust)" in summary
    assert "X: @alice" in summary
    assert "15 vouches" in summary

def test_format_flagged():
    profile = {
        "score": 0,
        "tier": "Flagged",
        "is_flagged": True,
    }
    summary = format_ethos_summary(profile)
    assert "Flagged" in summary

    badge = format_telegram_ethos_badge(profile)
    assert "Warning: Flagged Creator" in badge

def test_mocked_ethos_lookup(monkeypatch):
    fake_addr = "0x1234567890abcdef1234567890abcdef12345678"
    class FakeResponse:
        status_code = 200
        def json(self):
            return {
                "score": 950,
                "id": 42,
                "vouchCount": 8,
                "links": [{"service": "x.com", "username": "bob_crypto"}]
            }

    import requests
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: FakeResponse())

    profile = get_ethos_profile(fake_addr)
    assert profile["score"] == 950
    assert profile["tier"] == "Established"
    assert profile["x_handle"] == "bob_crypto"
    assert profile["vouch_count"] == 8
    assert "Score: 950" in profile["summary"]
