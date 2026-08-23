"""Tests for the persistent checkpoint store.

These cover the restart-resume contract that the in-memory-only state could not
provide: block watermarks, processed-mint sets, and Solana signature positions
must all survive a process restart, and a corrupt state file must degrade to a
cold start rather than crashing the bot on boot.
"""
import json
import os

import pytest

import checkpoint


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Point the store at a temp file and reset module globals between tests."""
    path = str(tmp_path / "state.json")
    monkeypatch.setattr(checkpoint, "STATE_FILE", path)
    monkeypatch.setattr(checkpoint, "_state", None)
    monkeypatch.setattr(checkpoint, "_seen_order", {})
    monkeypatch.setattr(checkpoint, "_dirty", False)
    monkeypatch.setattr(checkpoint, "_last_flush", 0.0)
    yield path


def _simulate_restart(monkeypatch):
    """Drop in-memory state so the next access reloads from disk."""
    monkeypatch.setattr(checkpoint, "_state", None)
    monkeypatch.setattr(checkpoint, "_seen_order", {})
    monkeypatch.setattr(checkpoint, "_dirty", False)
    monkeypatch.setattr(checkpoint, "_last_flush", 0.0)


# ── Block watermarks ─────────────────────────────────────────────────────────

def test_block_starts_unset():
    assert checkpoint.get_block("ethereum") is None


def test_block_survives_restart(monkeypatch, isolated_state):
    checkpoint.set_block("ethereum", 21_000_000, flush_now=True)
    _simulate_restart(monkeypatch)
    assert checkpoint.get_block("ethereum") == 21_000_000


def test_block_never_moves_backwards():
    """A lagging RPC replica reporting a stale tip must not rewind progress."""
    checkpoint.set_block("base", 500, flush_now=True)
    checkpoint.set_block("base", 400, flush_now=True)
    assert checkpoint.get_block("base") == 500


def test_block_rejects_equal_value():
    checkpoint.set_block("base", 500, flush_now=True)
    checkpoint.set_block("base", 500, flush_now=True)
    assert checkpoint.get_block("base") == 500


def test_block_ignores_garbage():
    checkpoint.set_block("base", "not-a-number", flush_now=True)  # type: ignore[arg-type]
    assert checkpoint.get_block("base") is None


def test_multiple_chains_are_independent(monkeypatch):
    checkpoint.set_block("ethereum", 100, flush_now=True)
    checkpoint.set_block("base", 200, flush_now=True)
    checkpoint.set_block("polygon", 300, flush_now=True)
    _simulate_restart(monkeypatch)
    assert checkpoint.all_blocks() == {"ethereum": 100, "base": 200, "polygon": 300}


# ── Processed-key sets ───────────────────────────────────────────────────────

def test_seen_survives_restart(monkeypatch):
    checkpoint.mark_seen("evm_contracts", "0xabc", flush_now=True)
    _simulate_restart(monkeypatch)
    assert checkpoint.was_seen("evm_contracts", "0xabc") is True
    assert checkpoint.was_seen("evm_contracts", "0xdef") is False


def test_seen_is_bounded(monkeypatch):
    """The persisted set must not grow without limit."""
    monkeypatch.setitem(checkpoint.SEEN_LIMITS, "evm_contracts", 5)
    _simulate_restart(monkeypatch)
    for i in range(12):
        checkpoint.mark_seen("evm_contracts", f"0x{i:04d}")
    assert checkpoint.seen_count("evm_contracts") == 5
    # Oldest evicted, newest retained.
    assert checkpoint.was_seen("evm_contracts", "0x0000") is False
    assert checkpoint.was_seen("evm_contracts", "0x0011") is True


def test_seen_sections_are_isolated():
    checkpoint.mark_seen("evm_contracts", "shared-key", flush_now=True)
    assert checkpoint.was_seen("evm_contracts", "shared-key") is True
    assert checkpoint.was_seen("btc_inscriptions", "shared-key") is False


def test_marking_same_key_twice_stores_once():
    checkpoint.mark_seen("evm_contracts", "0xabc")
    checkpoint.mark_seen("evm_contracts", "0xabc")
    assert checkpoint.seen_count("evm_contracts") == 1


def test_empty_key_is_ignored():
    checkpoint.mark_seen("evm_contracts", "")
    assert checkpoint.seen_count("evm_contracts") == 0
    assert checkpoint.was_seen("evm_contracts", "") is False


# ── Solana signature watermarks ──────────────────────────────────────────────

def test_signature_survives_restart(monkeypatch):
    checkpoint.set_signature("CoREENxT", "sig-abc-123", flush_now=True)
    _simulate_restart(monkeypatch)
    assert checkpoint.get_signature("CoREENxT") == "sig-abc-123"


def test_empty_signature_ignored():
    checkpoint.set_signature("CoREENxT", "", flush_now=True)
    assert checkpoint.get_signature("CoREENxT") is None


# ── Gemini per-key quota state ───────────────────────────────────────────────

def test_gemini_state_survives_restart(monkeypatch):
    checkpoint.set_gemini_key_state("AIza-secret", "2026-08-23", 480, 0.0, flush_now=True)
    _simulate_restart(monkeypatch)
    state = checkpoint.get_gemini_key_state("AIza-secret")
    assert state["count"] == 480
    assert state["date"] == "2026-08-23"


def test_raw_api_key_never_written_to_disk(isolated_state):
    """state.json lands in a public repo's working tree - keys must not leak."""
    secret = "AIzaSyREAL-SECRET-KEY-VALUE"
    checkpoint.set_gemini_key_state(secret, "2026-08-23", 5, 0.0, flush_now=True)
    raw = open(isolated_state, encoding="utf-8").read()
    assert secret not in raw
    assert checkpoint.fingerprint(secret) in raw


def test_fingerprint_is_stable_and_distinct():
    assert checkpoint.fingerprint("key-a") == checkpoint.fingerprint("key-a")
    assert checkpoint.fingerprint("key-a") != checkpoint.fingerprint("key-b")


def test_unknown_key_returns_zeroed_state():
    state = checkpoint.get_gemini_key_state("never-seen")
    assert state == {"date": "", "count": 0, "cooldown_until": 0.0}


# ── Durability and corruption handling ───────────────────────────────────────

def test_flush_is_debounced(isolated_state):
    """Rapid watermark updates must not rewrite the file every time."""
    checkpoint.set_block("ethereum", 1, flush_now=True)
    mtime_size = os.path.getsize(isolated_state)
    # Without force, a second write inside the debounce window is skipped.
    checkpoint.set_block("ethereum", 2)
    assert checkpoint.flush(force=False) is False
    assert checkpoint.flush(force=True) is True
    assert os.path.getsize(isolated_state) >= mtime_size


def test_corrupt_state_file_degrades_to_cold_start(isolated_state, monkeypatch):
    with open(isolated_state, "w", encoding="utf-8") as fh:
        fh.write("{ this is not valid json")
    _simulate_restart(monkeypatch)
    # Must not raise - a corrupt file cannot be allowed to block startup.
    assert checkpoint.get_block("ethereum") is None
    assert checkpoint.was_seen("evm_contracts", "0xabc") is False


def test_wrong_shaped_state_is_discarded_per_section(isolated_state, monkeypatch):
    payload = {
        "version": 1,
        "blocks": {"ethereum": "garbage", "base": 42},
        "signatures": {"prog": 12345},          # wrong type, dropped
        "seen": {"evm_contracts": "not-a-list"},  # wrong type, dropped
        "gemini": {"fp": "not-a-dict"},           # wrong type, dropped
    }
    with open(isolated_state, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    _simulate_restart(monkeypatch)
    assert checkpoint.get_block("ethereum") is None   # garbage dropped
    assert checkpoint.get_block("base") == 42         # valid entry kept
    assert checkpoint.get_signature("prog") is None
    assert checkpoint.seen_count("evm_contracts") == 0


def test_atomic_write_leaves_no_temp_file(isolated_state):
    checkpoint.set_block("ethereum", 7, flush_now=True)
    assert not os.path.exists(f"{isolated_state}.tmp")
    assert os.path.exists(isolated_state)


def test_state_file_is_valid_json(isolated_state):
    checkpoint.set_block("ethereum", 9, flush_now=True)
    checkpoint.mark_seen("evm_contracts", "0xabc", flush_now=True)
    with open(isolated_state, encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["version"] == checkpoint.VERSION
    assert data["blocks"]["ethereum"] == 9
    assert "0xabc" in data["seen"]["evm_contracts"]


# ── Test-isolation regression guard ──────────────────────────────────────────
# A test run once wrote 5,000 synthetic dedup keys into the repo's real
# state.json because no fixture redirected the store. conftest.py now isolates
# it globally; this asserts that isolation is actually in effect.

def test_state_file_is_redirected_away_from_the_repo(isolated_state):
    repo_state = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state.json"
    )
    assert checkpoint.STATE_FILE != repo_state
    checkpoint.mark_seen("evm_contracts", "isolation-probe", flush_now=True)
    assert os.path.exists(isolated_state)
    assert not os.path.exists(repo_state), (
        "tests must never write the production state file"
    )
