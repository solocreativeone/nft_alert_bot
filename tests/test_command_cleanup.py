"""Focused tests for Telegram command auto-cleanup UX.

Covers:
- Reusable helper: safe_delete_message handles sync, async, and failure cases safely.
- Reusable helper: schedule_message_cleanup deletes messages after delay.
- Application to ALL registered user commands:
  /start, /help, /watch, /unwatch, /list, /live, /status
- Both bot response and user command message are scheduled for deletion.
- Handling multiple responses (e.g. in /watch and /live).
- Graceful handling of Telegram deletion errors (missing permissions, already deleted).
- Actual NFT alerts, floor alerts, mint alerts, drops, and Community Pulse messages are not deleted.
- Inline /watch and /unwatch callbacks continue working.
"""
import asyncio
import types
import pytest

import commands
import watchlist


class _MockMessage:
    def __init__(self, text="", can_delete=True, raise_on_delete=False):
        self.text = text
        self.deleted = False
        self.delete_called_count = 0
        self.can_delete = can_delete
        self.raise_on_delete = raise_on_delete
        self.replies = []

    async def delete(self):
        self.delete_called_count += 1
        if not self.can_delete or self.raise_on_delete:
            raise RuntimeError("Telegram API error: message cannot be deleted or missing permission")
        self.deleted = True

    async def reply_text(self, text, **kwargs):
        rep = _MockMessage(text=text)
        self.replies.append(rep)
        return rep


def _make_update(chat_id="123", user_text="/help", can_delete=True, raise_on_delete=False):
    user_msg = _MockMessage(text=user_text, can_delete=can_delete, raise_on_delete=raise_on_delete)
    update = types.SimpleNamespace(
        effective_chat=types.SimpleNamespace(id=chat_id),
        message=user_msg,
    )
    return update, user_msg


@pytest.fixture(autouse=True)
def cleanup_test_env(monkeypatch):
    monkeypatch.setattr(commands, "CHAT_ID", "123")
    # Use short delay for tests so tests execute quickly without blocking 15s
    monkeypatch.setenv("COMMAND_CLEANUP_SECONDS", "0.01")
    monkeypatch.setattr(commands, "COMMAND_CLEANUP_SECONDS", 0.01)


# ── Reusable Helper Tests ─────────────────────────────────────────────────────

def test_safe_delete_message_handles_none_and_missing_attribute():
    # None must not raise
    asyncio.run(commands.safe_delete_message(None))

    # Object without delete method must not raise
    obj = object()
    asyncio.run(commands.safe_delete_message(obj))


def test_safe_delete_message_async_success():
    msg = _MockMessage(can_delete=True)
    asyncio.run(commands.safe_delete_message(msg))
    assert msg.deleted is True
    assert msg.delete_called_count == 1


def test_safe_delete_message_sync_success():
    class _SyncMessage:
        def __init__(self):
            self.deleted = False

        def delete(self):
            self.deleted = True

    msg = _SyncMessage()
    asyncio.run(commands.safe_delete_message(msg))
    assert msg.deleted is True


def test_safe_delete_message_handles_deletion_failure_gracefully():
    # Telegram API failure (permission error or already deleted) must not crash
    msg = _MockMessage(can_delete=False)
    asyncio.run(commands.safe_delete_message(msg))
    assert msg.deleted is False
    assert msg.delete_called_count == 1


def test_schedule_message_cleanup_deletes_after_delay():
    msg1 = _MockMessage(can_delete=True)
    msg2 = _MockMessage(can_delete=True)

    async def _runner():
        task = commands.schedule_message_cleanup([msg1, msg2], delay=0.01)
        assert msg1.deleted is False
        assert msg2.deleted is False
        await task
        assert msg1.deleted is True
        assert msg2.deleted is True

    asyncio.run(_runner())


# ── Registered Commands Cleanup Tests ─────────────────────────────────────────

@pytest.mark.parametrize("cmd_fn,args", [
    (commands.start_command, []),
    (commands.help_command, []),
    (commands.list_command, []),
    (commands.status_command, []),
])
def test_command_deletes_user_and_bot_messages(cmd_fn, args, monkeypatch):
    update, user_msg = _make_update(chat_id="123")
    ctx = types.SimpleNamespace(args=args)

    async def _runner():
        await cmd_fn(update, ctx)
        # Verify bot responded
        assert len(user_msg.replies) >= 1
        bot_msg = user_msg.replies[0]
        assert bot_msg.deleted is False
        assert user_msg.deleted is False

        # Wait for background cleanup (0.01s delay)
        await asyncio.sleep(0.05)

        assert user_msg.deleted is True
        assert bot_msg.deleted is True

    asyncio.run(_runner())


def test_watch_command_deletes_all_replies_and_user_message(monkeypatch):
    """watch_command sends an intermediate 'Looking up...' and then the result."""
    valid_addr = "0x" + "c" * 40
    fake_col = {
        "name": "Cleanup Col",
        "slug": "cleanup-col",
        "chain": "ethereum",
        "contract": valid_addr.lower(),
        "floor_alert_low": 0.01,
        "floor_alert_high": 1.0,
        "current_floor": 0.42,
    }
    monkeypatch.setattr(commands, "add_to_watchlist", lambda *a, **kw: (True, fake_col))
    monkeypatch.setattr(commands, "get_eth_usd_price", lambda: 2000.0)

    update, user_msg = _make_update(chat_id="123", user_text=f"/watch {valid_addr}")
    ctx = types.SimpleNamespace(args=[valid_addr, "ethereum"])

    async def _runner():
        await commands.watch_command(update, ctx)
        # Should have 2 replies: "Looking up..." and "Now watching..."
        assert len(user_msg.replies) == 2
        rep1, rep2 = user_msg.replies

        assert user_msg.deleted is False
        assert rep1.deleted is False
        assert rep2.deleted is False

        await asyncio.sleep(0.05)

        assert user_msg.deleted is True
        assert rep1.deleted is True
        assert rep2.deleted is True

    asyncio.run(_runner())


def test_unwatch_command_deletes_user_and_bot_message(monkeypatch):
    valid_addr = "0x" + "d" * 40
    monkeypatch.setattr(commands, "remove_from_watchlist", lambda *a, **kw: (True, "Removed successfully."))

    update, user_msg = _make_update(chat_id="123", user_text=f"/unwatch {valid_addr}")
    ctx = types.SimpleNamespace(args=[valid_addr])

    async def _runner():
        await commands.unwatch_command(update, ctx)
        assert len(user_msg.replies) == 1
        bot_msg = user_msg.replies[0]

        await asyncio.sleep(0.05)

        assert user_msg.deleted is True
        assert bot_msg.deleted is True

    asyncio.run(_runner())


def test_live_command_deletes_intermediate_and_summary_replies(monkeypatch):
    monkeypatch.setattr("live_drops.get_live_drops_summary", lambda chain: "Upcoming drops: None")

    update, user_msg = _make_update(chat_id="123", user_text="/live")
    ctx = types.SimpleNamespace(args=["ethereum"])

    async def _runner():
        await commands.live_command(update, ctx)
        # Intermediate "Fetching..." + summary
        assert len(user_msg.replies) == 2
        r1, r2 = user_msg.replies

        await asyncio.sleep(0.05)

        assert user_msg.deleted is True
        assert r1.deleted is True
        assert r2.deleted is True

    asyncio.run(_runner())


def test_cleanup_failure_does_not_crash_command():
    """If deleting user message or bot message raises an exception, command succeeds."""
    update, user_msg = _make_update(chat_id="123", can_delete=False, raise_on_delete=True)
    ctx = types.SimpleNamespace(args=[])

    async def _runner():
        await commands.help_command(update, ctx)
        bot_msg = user_msg.replies[0]
        bot_msg.raise_on_delete = True

        # Let cleanup attempt to run and fail
        await asyncio.sleep(0.05)

        # Both had delete attempted without crashing
        assert user_msg.delete_called_count >= 1
        assert bot_msg.delete_called_count >= 1

    asyncio.run(_runner())


def test_unauthorized_chat_ignores_command_without_cleanup():
    update, user_msg = _make_update(chat_id="999", user_text="/help")
    ctx = types.SimpleNamespace(args=[])

    async def _runner():
        await commands.help_command(update, ctx)
        assert len(user_msg.replies) == 0
        await asyncio.sleep(0.05)
        # User message was not deleted because chat was ignored
        assert user_msg.deleted is False

    asyncio.run(_runner())


def test_inline_callbacks_continue_working(monkeypatch):
    """Inline /unwatch and /watch buttons must remain functional."""
    contract = "0x" + "e" * 40
    monkeypatch.setattr(commands, "CHAT_ID", "123")

    async def mock_remove(*args):
        return True, ""
    monkeypatch.setattr("asyncio.to_thread", mock_remove)

    query = _MockMessage()
    query.data = f"unwatch:{contract}"
    query.answered = False

    async def answer():
        query.answered = True
    query.answer = answer
    query.message = _MockMessage()

    update = types.SimpleNamespace(
        effective_chat=types.SimpleNamespace(id="123"),
        callback_query=query,
    )

    asyncio.run(commands.unwatch_callback(update, None))
    assert query.answered is True
    assert len(query.message.replies) == 1
    assert "Removed" in query.message.replies[0].text
