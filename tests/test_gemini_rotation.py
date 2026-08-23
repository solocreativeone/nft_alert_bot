"""Tests for Gemini multi-key quota rotation.

The free tier caps each key at GEMINI_DAILY_LIMIT requests per UTC day. Because
is_worth_alerting() fails CLOSED on RATE_LIMITED, a single exhausted key silences
every alert for the rest of the day. These tests verify the bot rotates to the
next usable key instead, and that per-key quota state survives a restart.
"""
import asyncio
import time

import pytest

import checkpoint
import gemini_filter


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Fresh state file and a known 3-key pool for each test."""
    monkeypatch.setattr(checkpoint, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(checkpoint, "_state", None)
    monkeypatch.setattr(checkpoint, "_seen_order", {})
    monkeypatch.setattr(checkpoint, "_dirty", False)
    monkeypatch.setattr(checkpoint, "_last_flush", 0.0)

    monkeypatch.setattr(gemini_filter, "_key_pool", ["key-a", "key-b", "key-c"])
    monkeypatch.setattr(gemini_filter, "_active_index", 0)
    monkeypatch.setattr(gemini_filter, "_clients", {})
    monkeypatch.setattr(gemini_filter, "GEMINI_DAILY_LIMIT", 500)
    gemini_filter._score_cache.clear()
    yield


def _exhaust(key, count=500):
    checkpoint.set_gemini_key_state(key, gemini_filter._today(), count, 0.0, flush_now=True)


# ── Pool construction ────────────────────────────────────────────────────────

def test_pool_dedupes_and_puts_primary_first(monkeypatch):
    monkeypatch.setattr(gemini_filter, "GEMINI_API_KEY", "primary")
    monkeypatch.setattr(gemini_filter, "GEMINI_API_KEYS", ["extra", "primary", "  "])
    assert gemini_filter._build_key_pool() == ["primary", "extra"]


def test_pool_handles_no_keys(monkeypatch):
    monkeypatch.setattr(gemini_filter, "GEMINI_API_KEY", None)
    monkeypatch.setattr(gemini_filter, "GEMINI_API_KEYS", [])
    assert gemini_filter._build_key_pool() == []


def test_pool_strips_whitespace(monkeypatch):
    monkeypatch.setattr(gemini_filter, "GEMINI_API_KEY", "  spaced-key  ")
    monkeypatch.setattr(gemini_filter, "GEMINI_API_KEYS", [])
    assert gemini_filter._build_key_pool() == ["spaced-key"]


# ── Selection and rotation ───────────────────────────────────────────────────

def test_selects_first_key_when_all_fresh():
    assert gemini_filter.select_key() == "key-a"


def test_stays_on_active_key_while_it_has_quota():
    checkpoint.set_gemini_key_state("key-a", gemini_filter._today(), 100, 0.0, flush_now=True)
    assert gemini_filter.select_key() == "key-a"


def test_rotates_when_daily_limit_reached():
    _exhaust("key-a")
    assert gemini_filter.select_key() == "key-b"


def test_rotates_past_multiple_exhausted_keys():
    _exhaust("key-a")
    _exhaust("key-b")
    assert gemini_filter.select_key() == "key-c"


def test_returns_none_when_every_key_exhausted():
    for key in ("key-a", "key-b", "key-c"):
        _exhaust(key)
    assert gemini_filter.select_key() is None


def test_rotation_skips_key_on_cooldown():
    checkpoint.set_gemini_key_state(
        "key-a", gemini_filter._today(), 0, time.time() + 3600, flush_now=True
    )
    assert gemini_filter.select_key() == "key-b"


def test_expired_cooldown_makes_key_usable_again():
    checkpoint.set_gemini_key_state(
        "key-a", gemini_filter._today(), 0, time.time() - 10, flush_now=True
    )
    assert gemini_filter.select_key() == "key-a"


def test_active_index_advances_so_next_call_starts_on_the_new_key():
    _exhaust("key-a")
    assert gemini_filter.select_key() == "key-b"
    assert gemini_filter._active_index == 1
    # key-b still has quota, so we stay on it rather than retrying key-a.
    assert gemini_filter.select_key() == "key-b"


def test_stale_daily_count_rolls_over_to_a_new_day():
    """Yesterday's exhausted counter must not block a key today."""
    checkpoint.set_gemini_key_state("key-a", "2020-01-01", 500, 0.0, flush_now=True)
    assert gemini_filter.select_key() == "key-a"


def test_quota_state_survives_restart(monkeypatch):
    _exhaust("key-a")
    # Simulate a process restart: in-memory checkpoint state is dropped.
    monkeypatch.setattr(checkpoint, "_state", None)
    monkeypatch.setattr(checkpoint, "_seen_order", {})
    monkeypatch.setattr(gemini_filter, "_active_index", 0)
    # Must NOT go back to the exhausted key-a and start hammering it.
    assert gemini_filter.select_key() == "key-b"


# ── Cooldown accounting ──────────────────────────────────────────────────────

def test_daily_quota_error_marks_only_that_key():
    gemini_filter._trip_cooldown("429 RESOURCE_EXHAUSTED GenerateRequestsPerDay", "key-a")
    assert gemini_filter._key_is_available("key-a") is False
    assert gemini_filter._key_is_available("key-b") is True


def test_daily_quota_error_sets_long_cooldown():
    assert gemini_filter._compute_cooldown_seconds("quota exceeded PerDay") >= 3600.0


def test_per_minute_error_uses_short_cooldown():
    assert gemini_filter._compute_cooldown_seconds("429 rate limit") == 60.0


def test_retry_delay_is_honored():
    assert gemini_filter._compute_cooldown_seconds("429 retryDelay: 120") == 120.0


def test_daily_error_pins_count_to_the_limit():
    """A PerDay 429 means the key is spent even if our local count is low."""
    gemini_filter._trip_cooldown("RESOURCE_EXHAUSTED PerDay", "key-a")
    assert checkpoint.get_gemini_key_state("key-a")["count"] >= 500


# ── End-to-end scoring behaviour ─────────────────────────────────────────────

def _drive(monkeypatch, responses):
    """Run gemini_score_nft with a scripted per-key response map.

    responses maps api_key -> either an exception to raise or a JSON string.
    """
    calls = []

    class FakeResponse:
        def __init__(self, text):
            self.text = text

    async def fake_generate(client, prompt, api_key=None):
        calls.append(api_key)
        outcome = responses[api_key]
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)

    monkeypatch.setattr(gemini_filter, "_rate_limited_generate", fake_generate)
    monkeypatch.setattr(gemini_filter, "get_client", lambda api_key=None: "sentinel")
    result = asyncio.run(gemini_filter.gemini_score_nft({"contract": "0xrotate"}))
    return result, calls


def test_quota_error_rotates_and_retries_on_next_key(monkeypatch):
    quota_err = Exception("429 RESOURCE_EXHAUSTED GenerateRequestsPerDay")
    result, calls = _drive(monkeypatch, {
        "key-a": quota_err,
        "key-b": '{"score":88,"verdict":"LEGIT","reason":"clean"}',
    })
    assert calls == ["key-a", "key-b"], "must retry on the second key"
    assert result["verdict"] == "LEGIT"
    assert result["score"] == 88
    assert gemini_filter.is_worth_alerting(result, min_score=40) is True


def test_all_keys_quota_exhausted_returns_rate_limited(monkeypatch):
    quota_err = Exception("429 RESOURCE_EXHAUSTED PerDay")
    result, calls = _drive(monkeypatch, {
        "key-a": quota_err, "key-b": quota_err, "key-c": quota_err,
    })
    assert calls == ["key-a", "key-b", "key-c"]
    assert result["verdict"] == "RATE_LIMITED"
    assert gemini_filter.is_worth_alerting(result, min_score=40) is False


def test_non_quota_error_does_not_burn_other_keys(monkeypatch):
    result, calls = _drive(monkeypatch, {"key-a": Exception("500 internal error")})
    assert calls == ["key-a"], "a non-quota failure must not rotate"
    assert result["verdict"] == "ERROR"
    assert gemini_filter._key_is_available("key-b") is True


def test_successful_call_increments_only_the_active_key(monkeypatch):
    async def fake_generate(client, prompt, api_key=None):
        state = gemini_filter._key_state(api_key)
        state["count"] = int(state.get("count", 0)) + 1
        gemini_filter._save_key_state(api_key, state)

        class R:
            text = '{"score":70,"verdict":"LEGIT","reason":"ok"}'
        return R()

    monkeypatch.setattr(gemini_filter, "_rate_limited_generate", fake_generate)
    monkeypatch.setattr(gemini_filter, "get_client", lambda api_key=None: "sentinel")
    asyncio.run(gemini_filter.gemini_score_nft({"contract": "0xcount"}))
    assert checkpoint.get_gemini_key_state("key-a")["count"] == 1
    assert checkpoint.get_gemini_key_state("key-b")["count"] == 0


def test_no_keys_configured_returns_unknown_not_rate_limited(monkeypatch):
    """Users with no Gemini key must keep receiving alerts (UNKNOWN passes)."""
    monkeypatch.setattr(gemini_filter, "_key_pool", [])
    monkeypatch.setattr(gemini_filter, "get_client", lambda api_key=None: None)
    result = asyncio.run(gemini_filter.gemini_score_nft({"contract": "0xnokey"}))
    assert result["verdict"] == "UNKNOWN"
    assert gemini_filter.is_worth_alerting(result, min_score=40) is True


def test_exhausted_pool_blocks_before_making_a_call(monkeypatch):
    """When every key is already known-spent we must not attempt a request."""
    for key in ("key-a", "key-b", "key-c"):
        _exhaust(key)

    async def boom(client, prompt, api_key=None):
        raise AssertionError("must not call the API when all keys are exhausted")

    monkeypatch.setattr(gemini_filter, "_rate_limited_generate", boom)
    monkeypatch.setattr(gemini_filter, "get_client", lambda api_key=None: "sentinel")
    result = asyncio.run(gemini_filter.gemini_score_nft({"contract": "0xspent"}))
    assert result["verdict"] == "RATE_LIMITED"


# ── Observability ────────────────────────────────────────────────────────────

def test_pool_status_reports_usage_without_leaking_keys():
    checkpoint.set_gemini_key_state("key-a", gemini_filter._today(), 250, 0.0, flush_now=True)
    _exhaust("key-b")
    status = gemini_filter.pool_status()
    assert status["total_keys"] == 3
    assert status["available_keys"] == 2   # key-a (250/500) and key-c (0/500)
    rendered = repr(status)
    for key in ("key-a", "key-b", "key-c"):
        assert key not in rendered
    assert status["keys"][0]["used_today"] == 250
    assert status["keys"][1]["available"] is False
