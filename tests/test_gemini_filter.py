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
