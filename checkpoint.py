"""checkpoint.py - Persistent scan state so restarts resume instead of rescanning.

Every scanner previously kept its position and dedup history in module-level
dicts and sets, so a restart reset the bot to a cold start: block watermarks went
back to ``None`` (rescanning a fresh window) and the alerted-contract sets were
empty (re-alerting anything still inside that window). This module gives all
scanners one durable store.

Layout of state.json::

    {
      "version": 1,
      "blocks":     {"<chain>": <last fully processed block int>},
      "signatures": {"<solana program id>": "<last seen signature>"},
      "seen":       {"<section>": ["<key>", ...]},
      "gemini":     {"<key fingerprint>": {"date", "count", "cooldown_until"}}
    }

Ordering contract that makes resume safe
----------------------------------------
A block watermark must only be committed AFTER every alert derived from that
range has been sent. Committing earlier means a crash mid-evaluation loses those
drops permanently. Committing later means a crash re-scans the range, and the
persisted ``seen`` sets suppress the duplicate alerts. That gives at-least-once
scanning with exactly-once alerting, which is the safe direction to fail in.

Durability
----------
Writes are atomic (temp file + ``os.replace``) and debounced: callers mark state
dirty freely and the file is rewritten at most once per FLUSH_INTERVAL_SECONDS.
An atexit hook plus SIGTERM/SIGINT handlers force a final flush so a clean
shutdown never loses the last few seconds of progress.

Secrets are never written. Gemini keys are stored as a truncated SHA-256
fingerprint, so state.json stays safe even though the repo is public.
"""
import atexit
import hashlib
import json
import os
import signal
import time
from collections import deque

VERSION = 1

STATE_FILE = os.environ.get(
    "NFT_BOT_STATE_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json"),
)

# Rewriting the file on every watermark change would mean a write per chain per
# minute; on Android's FUSE-backed storage that is needlessly slow. Debounce.
FLUSH_INTERVAL_SECONDS = 10.0

# Per-section caps on persisted dedup history. Bounded so state.json cannot grow
# without limit and the debounced rewrite stays cheap.
SEEN_LIMITS = {
    "evm_contracts": 5000,
    "solana_mints": 5000,
    "solana_signatures": 5000,
    "btc_inscriptions": 2000,
    "calendar_links": 5000,
}
DEFAULT_SEEN_LIMIT = 2000

_state = None
_seen_order = {}   # section -> deque preserving insertion order for eviction
_dirty = False
_last_flush = 0.0
_hooks_installed = False


def _empty_state():
    return {"version": VERSION, "blocks": {}, "signatures": {}, "seen": {}, "gemini": {}}


def _coerce(raw):
    """Normalize a loaded payload, dropping anything of the wrong shape.

    A truncated or hand-edited state file must degrade to a cold start for the
    affected section rather than raising and taking the bot down on boot.
    """
    state = _empty_state()
    if not isinstance(raw, dict):
        return state

    blocks = raw.get("blocks")
    if isinstance(blocks, dict):
        for chain, value in blocks.items():
            try:
                state["blocks"][str(chain)] = int(value)
            except (TypeError, ValueError):
                continue

    signatures = raw.get("signatures")
    if isinstance(signatures, dict):
        for program, sig in signatures.items():
            if isinstance(sig, str) and sig:
                state["signatures"][str(program)] = sig

    seen = raw.get("seen")
    if isinstance(seen, dict):
        for section, keys in seen.items():
            if not isinstance(keys, list):
                continue
            limit = SEEN_LIMITS.get(section, DEFAULT_SEEN_LIMIT)
            cleaned = [str(k) for k in keys if isinstance(k, (str, int))][-limit:]
            state["seen"][str(section)] = cleaned

    gemini = raw.get("gemini")
    if isinstance(gemini, dict):
        for fingerprint, entry in gemini.items():
            if not isinstance(entry, dict):
                continue
            try:
                state["gemini"][str(fingerprint)] = {
                    "date": str(entry.get("date") or ""),
                    "count": int(entry.get("count", 0) or 0),
                    "cooldown_until": float(entry.get("cooldown_until", 0) or 0),
                }
            except (TypeError, ValueError):
                continue

    return state


def load(path: str | None = None, force: bool = False) -> dict:
    """Load state from disk once and return the live in-memory dict."""
    global _state, _seen_order
    if _state is not None and not force:
        return _state

    target = path or STATE_FILE
    raw = None
    if os.path.exists(target):
        try:
            with open(target, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except Exception as e:
            print(f"[Checkpoint] Could not read {target}: {e} - starting cold")

    _state = _coerce(raw)
    _seen_order = {
        section: deque(keys, maxlen=SEEN_LIMITS.get(section, DEFAULT_SEEN_LIMIT))
        for section, keys in _state["seen"].items()
    }

    if raw is not None:
        print(
            f"[Checkpoint] Resumed: {len(_state['blocks'])} chain watermark(s), "
            f"{sum(len(v) for v in _state['seen'].values())} processed key(s)"
        )
    else:
        print("[Checkpoint] No prior state - first run, seeding watermarks from chain tips")

    _install_hooks()
    return _state


def _install_hooks():
    """Flush on interpreter exit and on SIGTERM/SIGINT."""
    global _hooks_installed
    if _hooks_installed:
        return
    _hooks_installed = True
    atexit.register(lambda: flush(force=True))

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            previous = signal.getsignal(sig)

            def _handler(signum, frame, _previous=previous):
                flush(force=True)
                if callable(_previous):
                    _previous(signum, frame)
                else:
                    raise KeyboardInterrupt

            signal.signal(sig, _handler)
        except (ValueError, OSError):
            # Not on the main thread, or the platform rejects the handler.
            pass


def _mark_dirty():
    global _dirty
    _dirty = True


def flush(force: bool = False, path: str | None = None) -> bool:
    """Persist state atomically. Returns True when a write actually happened."""
    global _dirty, _last_flush
    if _state is None or not _dirty:
        return False
    now = time.time()
    if not force and (now - _last_flush) < FLUSH_INTERVAL_SECONDS:
        return False

    target = path or STATE_FILE
    tmp = f"{target}.tmp"
    payload = dict(_state)
    payload["version"] = VERSION
    payload["seen"] = {section: list(order) for section, order in _seen_order.items()}

    try:
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
        _state["seen"] = payload["seen"]
        _dirty = False
        _last_flush = now
        return True
    except Exception as e:
        print(f"[Checkpoint] Save failed for {target}: {e}")
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass
        return False


# ── Block watermarks ─────────────────────────────────────────────────────────

def get_block(chain: str):
    """Last fully processed block for a chain, or None if never scanned."""
    return load()["blocks"].get(chain)


def set_block(chain: str, block: int, flush_now: bool = False):
    """Commit a chain watermark. Call only after alerts for the range are sent."""
    state = load()
    try:
        block = int(block)
    except (TypeError, ValueError):
        return
    # Never move a watermark backwards: a lagging RPC replica reporting a stale
    # tip would otherwise rewind progress and cause a re-scan.
    if state["blocks"].get(chain) is not None and block <= state["blocks"][chain]:
        return
    state["blocks"][chain] = block
    _mark_dirty()
    flush(force=flush_now)


def all_blocks() -> dict:
    return dict(load()["blocks"])


# ── Solana signature watermarks ──────────────────────────────────────────────

def get_signature(program_id: str):
    return load()["signatures"].get(program_id)


def set_signature(program_id: str, signature: str, flush_now: bool = False):
    if not signature:
        return
    load()["signatures"][program_id] = signature
    _mark_dirty()
    flush(force=flush_now)


# ── Processed-key sets ───────────────────────────────────────────────────────

def was_seen(section: str, key: str) -> bool:
    """True if this key was already processed, in this run or a previous one."""
    if not key:
        return False
    load()
    order = _seen_order.get(section)
    return bool(order) and key in order


def mark_seen(section: str, key: str, flush_now: bool = False):
    """Record a processed key, evicting the oldest once the section cap is hit."""
    if not key:
        return
    load()
    order = _seen_order.get(section)
    if order is None:
        order = deque(maxlen=SEEN_LIMITS.get(section, DEFAULT_SEEN_LIMIT))
        _seen_order[section] = order
    if key in order:
        return
    order.append(key)   # deque(maxlen=...) evicts the oldest automatically
    _mark_dirty()
    flush(force=flush_now)


def seen_count(section: str) -> int:
    load()
    return len(_seen_order.get(section, ()))


# ── Gemini per-key quota state ───────────────────────────────────────────────

def fingerprint(api_key: str) -> str:
    """Stable non-reversible id for an API key, safe to write to disk."""
    return hashlib.sha256((api_key or "").encode("utf-8")).hexdigest()[:12]


def get_gemini_key_state(api_key: str) -> dict:
    fp = fingerprint(api_key)
    entry = load()["gemini"].get(fp)
    if not entry:
        return {"date": "", "count": 0, "cooldown_until": 0.0}
    return dict(entry)


def set_gemini_key_state(api_key: str, date: str, count: int, cooldown_until: float,
                         flush_now: bool = False):
    load()["gemini"][fingerprint(api_key)] = {
        "date": str(date or ""),
        "count": int(count),
        "cooldown_until": float(cooldown_until),
    }
    _mark_dirty()
    flush(force=flush_now)


# ── Test / maintenance helpers ───────────────────────────────────────────────

def reset(path: str | None = None):
    """Drop in-memory state (and the backing file). Intended for tests."""
    global _state, _seen_order, _dirty, _last_flush
    _state = None
    _seen_order = {}
    _dirty = False
    _last_flush = 0.0
    target = path or STATE_FILE
    if os.path.exists(target):
        try:
            os.remove(target)
        except Exception:
            pass


def use_path(path: str):
    """Point the store at a different file and reload. Intended for tests."""
    global STATE_FILE, _state, _seen_order, _dirty, _last_flush
    STATE_FILE = path
    _state = None
    _seen_order = {}
    _dirty = False
    _last_flush = 0.0
    return load()
