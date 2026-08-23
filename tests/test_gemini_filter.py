"""Tests for gemini_filter's alert-gating logic. These cover the pure decision
functions (is_worth_alerting / verdict_badge) and require no Gemini API call."""
import gemini_filter


# ── is_worth_alerting: fail CLOSED on anything we couldn't actually vet ──────

def test_likely_rug_blocked():
    assert gemini_filter.is_worth_alerting({"verdict": "LIKELY_RUG", "score": 5}, min_score=40) is False


def test_rate_limited_blocked():
    # Quota exhausted — stay quiet rather than flood unvetted drops.
    assert gemini_filter.is_worth_alerting({"verdict": "RATE_LIMITED", "score": 0}, min_score=40) is False


def test_error_blocked():
    # API failure now fails CLOSED (was UNKNOWN/pass before the fix).
    assert gemini_filter.is_worth_alerting({"verdict": "ERROR", "score": 0}, min_score=40) is False


def test_unknown_passes():
    # Filter intentionally disabled / unconfigured — users without a key still alert.
    assert gemini_filter.is_worth_alerting({"verdict": "UNKNOWN", "score": 50}, min_score=40) is True


def test_legit_respects_min_score():
    assert gemini_filter.is_worth_alerting({"verdict": "LEGIT", "score": 80}, min_score=40) is True
    assert gemini_filter.is_worth_alerting({"verdict": "LEGIT", "score": 20}, min_score=40) is False


def test_legit_at_exact_min_score_passes():
    assert gemini_filter.is_worth_alerting({"verdict": "LEGIT", "score": 40}, min_score=40) is True


# ── verdict_badge rendering ──────────────────────────────────────────────────

def test_badge_error():
    badge = gemini_filter.verdict_badge({"verdict": "ERROR", "reason": "boom"})
    assert "AI Audit Failed" in badge


def test_badge_rate_limited():
    badge = gemini_filter.verdict_badge({"verdict": "RATE_LIMITED"})
    assert "quota" in badge.lower()


def test_badge_likely_rug():
    badge = gemini_filter.verdict_badge({"verdict": "LIKELY_RUG", "score": 5})
    assert "Rug" in badge


def test_badge_legit():
    badge = gemini_filter.verdict_badge({"verdict": "LEGIT", "score": 90, "reason": "clean"})
    assert "Legit" in badge
    assert "90" in badge


# ── response normalization ───────────────────────────────────────────────────
# gemini_score_nft used to do `json.loads(raw)` then index result['verdict'] and
# result['score'] directly in a print(). A response missing either key raised a
# KeyError INSIDE the try block, so a perfectly good LEGIT verdict fell through
# to the generic handler and was discarded as ERROR, suppressing the alert.
# `response.text` is also None on a safety block / MAX_TOKENS finish, which
# crashed on .strip().

import asyncio

import pytest


def _run_with_response(monkeypatch, text):
    """Drive gemini_score_nft with a canned model response."""
    class FakeResponse:
        pass

    FakeResponse.text = text

    async def fake_generate(client, prompt, api_key=None):
        return FakeResponse()

    monkeypatch.setattr(gemini_filter, "_rate_limited_generate", fake_generate)
    # A single-key pool with a live client, so select_key() returns a usable key
    # and get_client() short-circuits to the sentinel instead of calling genai.
    monkeypatch.setattr(gemini_filter, "_key_pool", ["test-key"])
    monkeypatch.setattr(gemini_filter, "_clients", {"test-key": "sentinel-client"})
    monkeypatch.setattr(gemini_filter, "_active_index", 0)
    monkeypatch.setattr(gemini_filter, "_key_is_available", lambda key, now=None: True)
    monkeypatch.setattr(gemini_filter, "_save_key_state", lambda *a, **k: None)
    monkeypatch.setattr(
        gemini_filter, "_key_state",
        lambda key: {"date": "", "count": 0, "cooldown_until": 0.0},
    )
    gemini_filter._score_cache.clear()
    return asyncio.run(gemini_score_nft_unique())


_counter = {"n": 0}


def gemini_score_nft_unique():
    """Each call uses a fresh contract id so the score cache never interferes."""
    _counter["n"] += 1
    return gemini_filter.gemini_score_nft({"contract": f"0xtest{_counter['n']:04d}"})


def test_missing_score_key_keeps_verdict(monkeypatch):
    res = _run_with_response(monkeypatch, '{"verdict":"LEGIT","reason":"ok"}')
    assert res["verdict"] == "LEGIT", "must not be discarded as ERROR"
    assert res["score"] == 0


def test_score_as_string_is_coerced(monkeypatch):
    res = _run_with_response(monkeypatch, '{"score":"85","verdict":"LEGIT","reason":"ok"}')
    assert res["score"] == 85
    assert gemini_filter.is_worth_alerting(res, min_score=40) is True


def test_score_is_clamped_to_range(monkeypatch):
    res = _run_with_response(monkeypatch, '{"score":9999,"verdict":"LEGIT","reason":"x"}')
    assert res["score"] == 100


def test_verdict_is_uppercased(monkeypatch):
    res = _run_with_response(monkeypatch, '{"score":70,"verdict":"legit","reason":"x"}')
    assert res["verdict"] == "LEGIT"
    assert gemini_filter.is_worth_alerting(res, min_score=40) is True


def test_markdown_fenced_json_is_parsed(monkeypatch):
    res = _run_with_response(
        monkeypatch, '```json\n{"score":55,"verdict":"SUSPICIOUS","reason":"y"}\n```'
    )
    assert res["verdict"] == "SUSPICIOUS"
    assert res["score"] == 55


@pytest.mark.parametrize(
    "payload",
    [
        None,                              # safety block / MAX_TOKENS
        "",                                # empty completion
        "Sure! Here is my analysis.",      # prose instead of JSON
        "[1,2,3]",                         # valid JSON, wrong shape
    ],
)
def test_unusable_responses_fail_closed(monkeypatch, payload):
    res = _run_with_response(monkeypatch, payload)
    assert res["verdict"] == "ERROR"
    assert res["score"] == 0
    assert gemini_filter.is_worth_alerting(res, min_score=40) is False
