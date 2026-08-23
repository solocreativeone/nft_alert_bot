from commands import ETH_ADDRESS_PATTERN


def test_accepts_valid_addresses():
    assert ETH_ADDRESS_PATTERN.match("0x" + "a" * 40)
    assert ETH_ADDRESS_PATTERN.match("0xBC4CA0EdA7647A8aB7C2061c2E118A18a936f13D")


def test_rejects_wrong_length():
    assert not ETH_ADDRESS_PATTERN.match("0x" + "a" * 39)
    assert not ETH_ADDRESS_PATTERN.match("0x" + "a" * 41)


def test_rejects_missing_prefix():
    assert not ETH_ADDRESS_PATTERN.match("a" * 40)


def test_rejects_non_hex():
    assert not ETH_ADDRESS_PATTERN.match("0x" + "g" * 40)


# ── /status command ──────────────────────────────────────────────────────────
# Reports checkpoint position and Gemini key quota. Must never leak a raw API
# key into the chat, and must be inert for chats other than the configured one.

import asyncio
import types

import checkpoint
import commands
import gemini_filter


class _FakeMessage:
    def __init__(self):
        self.sent = []

    async def reply_text(self, text, **kwargs):
        self.sent.append(text)


def _fake_update(chat_id):
    msg = _FakeMessage()
    return types.SimpleNamespace(
        effective_chat=types.SimpleNamespace(id=chat_id), message=msg
    ), msg


def test_status_reports_watermarks_and_key_quota(monkeypatch):
    monkeypatch.setattr(commands, "CHAT_ID", "123")
    checkpoint.set_block("ethereum", 21_000_000, flush_now=True)
    checkpoint.mark_seen("evm_contracts", "0xabc", flush_now=True)
    monkeypatch.setattr(gemini_filter, "_key_pool", ["SECRET_KEY_A", "SECRET_KEY_B"])
    monkeypatch.setattr(gemini_filter, "_active_index", 0)

    update, msg = _fake_update("123")
    asyncio.run(commands.status_command(update, None))

    assert len(msg.sent) == 1
    body = msg.sent[0]
    assert "21000000" in body
    assert "ethereum" in body
    assert "2/2 available" in body
    # A raw key must never reach the chat.
    assert "SECRET_KEY_A" not in body
    assert "SECRET_KEY_B" not in body


def test_status_ignores_other_chats(monkeypatch):
    monkeypatch.setattr(commands, "CHAT_ID", "123")
    update, msg = _fake_update("999")
    asyncio.run(commands.status_command(update, None))
    assert msg.sent == [], "must not respond to an unauthorized chat"


def test_status_handles_cold_start(monkeypatch):
    monkeypatch.setattr(commands, "CHAT_ID", "123")
    monkeypatch.setattr(gemini_filter, "_key_pool", [])
    update, msg = _fake_update("123")
    asyncio.run(commands.status_command(update, None))
    assert "cold start" in msg.sent[0]


def test_status_command_is_registered():
    """A handler that is written but never wired up is invisible to the user."""
    import inspect
    src = inspect.getsource(commands.build_app)
    assert '"status"' in src and "status_command" in src
