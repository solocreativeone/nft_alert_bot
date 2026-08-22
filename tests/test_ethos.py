import pytest
import ethos
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
    """v2 /user/by/address shape: score, stats.vouch, stats.review, userkeys."""
    fake_addr = "0x1234567890abcdef1234567890abcdef12345678"
    class FakeResponse:
        status_code = 200
        def json(self):
            return {
                "score": 950,
                "id": 42,
                "profileId": None,
                "username": "bob_crypto",
                "userkeys": ["service:x.com:2259434528", f"address:{fake_addr}"],
                "stats": {
                    "review": {"received": {"negative": 0, "neutral": 1, "positive": 12}},
                    "vouch": {"received": {"count": 8, "amountWeiTotal": "0"}},
                },
                "links": {"profile": "https://app.ethos.network/profile/0x1234"},
            }

    import requests
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: FakeResponse())
    ethos._ethos_cache.clear()

    profile = get_ethos_profile(fake_addr)
    assert profile["score"] == 950
    assert profile["tier"] == "Established"
    assert profile["x_handle"] == "bob_crypto"
    assert profile["vouch_count"] == 8
    assert profile["is_flagged"] is False
    assert profile["ethos_url"] == "https://app.ethos.network/profile/0x1234"
    assert "Score: 950" in profile["summary"]


def test_lookup_falls_back_to_score_endpoint_on_404(monkeypatch):
    """A wallet with no Ethos profile 404s on /user, so score must come from /score."""
    class Resp404:
        status_code = 404
        def json(self):
            return {}

    class RespScore:
        status_code = 200
        def json(self):
            return {"score": 730, "level": "neutral"}

    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return RespScore() if "/score/" in url else Resp404()

    import requests
    monkeypatch.setattr(requests, "get", fake_get)
    ethos._ethos_cache.clear()

    profile = get_ethos_profile("0xabcdefabcdefabcdefabcdefabcdefabcdefabcd")
    assert profile["score"] == 730
    assert profile["tier"] == "Established"
    assert profile["is_flagged"] is False
    assert any("/user/by/address/" in u for u in calls), "must try the v2 user endpoint"
    assert any("/score/address" in u for u in calls), "must fall back to the score endpoint"


def test_dead_v1_endpoint_is_not_used():
    """Regression: /api/v1/users/address: is dead (404) and silently blanked
    vouches, socials and flags. Ensure we don't reintroduce it.

    Checks the URL constants rather than the module source, so the explanatory
    comment naming the dead endpoint doesn't trip the assertion.
    """
    assert "/api/v1/users/address" not in ethos.ETHOS_USER_URL
    assert "/api/v1/users/address" not in ethos.ETHOS_SCORE_URL
    assert "/api/v2/user/by/address/" in ethos.ETHOS_USER_URL
    assert "/api/v2/score/address" in ethos.ETHOS_SCORE_URL


def test_extract_socials_from_userkeys():
    x, fc, dc, tg = ethos._extract_socials(
        {"username": "cobie", "userkeys": ["service:x.com:2259434528"]}
    )
    assert x == "cobie"
    assert fc is None
    # address-only userkeys must not produce a handle
    assert ethos._extract_socials({"username": None, "userkeys": ["address:0xabc"]}) == (
        None,
        None,
        None,
        None,
    )


def test_is_flagged_requires_negatives_to_outweigh_positives():
    # Real data: vitalik.eth has 3 negative vs 1077 positive reviews. Flagging
    # on "any negative" would hard-block a legitimate creator in drops.py.
    assert ethos._is_flagged({"negative": 3, "positive": 1077}, {}) is False
    assert ethos._is_flagged({"negative": 0, "positive": 6}, {}) is False
    assert ethos._is_flagged({"negative": 5, "positive": 1}, {}) is True
    assert ethos._is_flagged({}, {"isSlashed": True}) is True
