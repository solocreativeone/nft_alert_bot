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

# ── Watch / Unwatch / List commands ──────────────────────────────────────────

def test_watch_command_requires_args(monkeypatch):
    monkeypatch.setattr(commands, "CHAT_ID", "123")
    update, msg = _fake_update("123")
    ctx = types.SimpleNamespace(args=[])
    asyncio.run(commands.watch_command(update, ctx))
    assert "Usage: /watch" in msg.sent[0]

def test_watch_command_invalid_address(monkeypatch):
    monkeypatch.setattr(commands, "CHAT_ID", "123")
    update, msg = _fake_update("123")
    ctx = types.SimpleNamespace(args=["0xinvalid", "ethereum"])
    asyncio.run(commands.watch_command(update, ctx))
    assert "Invalid contract address" in msg.sent[0]

def test_unwatch_command_requires_args(monkeypatch):
    monkeypatch.setattr(commands, "CHAT_ID", "123")
    update, msg = _fake_update("123")
    ctx = types.SimpleNamespace(args=[])
    asyncio.run(commands.unwatch_command(update, ctx))
    assert "Usage: /unwatch" in msg.sent[0]

def test_list_command_empty(monkeypatch):
    monkeypatch.setattr(commands, "CHAT_ID", "123")
    monkeypatch.setattr(commands, "get_watchlist", lambda: [])
    update, msg = _fake_update("123")
    asyncio.run(commands.list_command(update, None))
    assert "No collections currently being watched" in msg.sent[0]

def test_list_command_with_items(monkeypatch):
    monkeypatch.setattr(commands, "CHAT_ID", "123")
    monkeypatch.setattr(commands, "get_watchlist", lambda: [{
        "name": "Test Col",
        "slug": "test-col",
        "chain": "ethereum",
        "contract": "0x1234567890123456789012345678901234567890",
        "current_floor": 0.5
    }])
    monkeypatch.setattr(commands, "get_eth_usd_price", lambda: 2000.0)
    update, msg = _fake_update("123")
    asyncio.run(commands.list_command(update, None))
    assert "Test Col" in msg.sent[0]
    assert "0x1234...7890" in msg.sent[0]
    assert "Floor: 0.5" in msg.sent[0]

class _FakeCallbackQuery:
    def __init__(self, data):
        self.data = data
        self.message = _FakeMessage()
        self.answered = False

    async def answer(self):
        self.answered = True

def _fake_callback_update(chat_id, data):
    query = _FakeCallbackQuery(data)
    return types.SimpleNamespace(
        effective_chat=types.SimpleNamespace(id=chat_id),
        callback_query=query
    ), query

def test_watch_callback_invalid(monkeypatch):
    monkeypatch.setattr(commands, "CHAT_ID", "123")
    update, query = _fake_callback_update("123", "watch:ethereum:0xinvalid")
    asyncio.run(commands.watch_callback(update, None))
    assert query.answered
    assert "Invalid contract address" in query.message.sent[0]

def test_unwatch_callback_valid(monkeypatch):
    monkeypatch.setattr(commands, "CHAT_ID", "123")
    update, query = _fake_callback_update("123", "unwatch:0x1234567890123456789012345678901234567890")

    async def mock_remove(*args):
        return True, ""
    monkeypatch.setattr("asyncio.to_thread", mock_remove)

    asyncio.run(commands.unwatch_callback(update, None))
    assert query.answered
    assert "Removed 0x1234...7890" in query.message.sent[0]


# ── Inspect command tests ───────────────────────────────────────────────────

def test_inspect_command_unsupported_chain_arc(monkeypatch):
    monkeypatch.setattr(commands, "CHAT_ID", "123")
    contract = "0x" + "a" * 40
    update, msg = _fake_update("123")
    ctx = types.SimpleNamespace(args=[contract, "arc"])

    asyncio.run(commands.inspect_command(update, ctx))

    assert len(msg.sent) == 2
    assert "🔎 Inspecting contract security…" in msg.sent[0]
    result_text = msg.sent[1]
    assert "🔎 Contract Inspection" in result_text
    assert "Chain: Arc" in result_text
    assert "Risk: ⚪ Not Assessed" in result_text
    assert "Provider:\n• Security provider does not currently support Arc." in result_text
    assert "Automated assessment. Not a guarantee of safety." not in result_text


def test_inspect_command_unsupported_chain_robinhood(monkeypatch):
    monkeypatch.setattr(commands, "CHAT_ID", "123")
    contract = "0x" + "a" * 40
    update, msg = _fake_update("123")
    ctx = types.SimpleNamespace(args=[contract, "robinhood"])

    asyncio.run(commands.inspect_command(update, ctx))

    result_text = msg.sent[1]
    assert "Chain: Robinhood" in result_text
    assert "Risk: ⚪ Not Assessed" in result_text
    assert "Provider:\n• Security provider does not currently support Robinhood." in result_text
    assert "Automated assessment. Not a guarantee of safety." not in result_text


def test_inspect_command_supported_chain_valid_response(monkeypatch):
    monkeypatch.setattr(commands, "CHAT_ID", "123")
    contract = "0x" + "b" * 40
    update, msg = _fake_update("123")
    ctx = types.SimpleNamespace(args=[contract, "arbitrum"])

    fake_result = {
        "contract": contract.lower(),
        "chain": "arbitrum",
        "risk": "looks_legit",
        "flags": [],
        "details": {
            "token": {"name": "TestToken", "symbol": "TT"},
            "verified": True,
            "proxy": False,
            "honeypot": False,
        }
    }
    monkeypatch.setattr(commands, "inspect_security_contract", lambda *args, **kwargs: fake_result)

    asyncio.run(commands.inspect_command(update, ctx))

    result_text = msg.sent[1]
    assert "Collection: TestToken" in result_text
    assert "Chain: Arbitrum" in result_text
    assert "Risk: 🟢 Looks Legit" in result_text
    assert "• Verified: Yes" in result_text
    assert "• Proxy: No" in result_text
    assert "• Honeypot indicators: None detected" in result_text
    assert "⚠️ Automated assessment. Not a guarantee of safety." in result_text


def test_inspect_command_provider_failure(monkeypatch):
    monkeypatch.setattr(commands, "CHAT_ID", "123")
    contract = "0x" + "c" * 40
    update, msg = _fake_update("123")
    ctx = types.SimpleNamespace(args=[contract, "ethereum"])

    def fail(*args, **kwargs):
        raise RuntimeError("API timeout")

    monkeypatch.setattr(commands, "inspect_security_contract", fail)

    asyncio.run(commands.inspect_command(update, ctx))

    result_text = msg.sent[1]
    assert "⚠️ Security provider unavailable.\nPlease try again later." in result_text


def test_inspect_command_replaces_temporary_message(monkeypatch):
    monkeypatch.setattr(commands, "CHAT_ID", "123")
    contract = "0x" + "d" * 40

    class MockStatusMsg:
        def __init__(self):
            self.text = "🔎 Inspecting contract security…"

        async def edit_text(self, text, **kwargs):
            self.text = text

    class MockUserMsg:
        def __init__(self):
            self.status_msg = MockStatusMsg()
            self.sent_count = 0

        async def reply_text(self, text, **kwargs):
            self.sent_count += 1
            return self.status_msg

    mock_msg = MockUserMsg()
    update = types.SimpleNamespace(
        effective_chat=types.SimpleNamespace(id="123"),
        message=mock_msg,
    )
    ctx = types.SimpleNamespace(args=[contract, "arc"])

    asyncio.run(commands.inspect_command(update, ctx))

    # The temporary message was edited in place, avoiding duplicate messages
    assert mock_msg.sent_count == 1
    assert "🔎 Contract Inspection" in mock_msg.status_msg.text
    assert "Security provider does not currently support Arc." in mock_msg.status_msg.text


def test_inspect_command_honeypot_404_token_not_found(monkeypatch, tmp_path):
    import security
    monkeypatch.setattr(commands, "CHAT_ID", "123")
    checkpoint.use_path(str(tmp_path / "state.json"))
    contract = "0x" + "e" * 40
    update, msg = _fake_update("123")
    ctx = types.SimpleNamespace(args=[contract, "base"])

    class Mock404Response:
        status_code = 404
        status = 404

        def raise_for_status(self):
            raise RuntimeError("404 Client Error: Not Found")

        def json(self):
            return {"code": 404, "error": "Token not found"}

    monkeypatch.setattr(
        commands,
        "inspect_security_contract",
        lambda *args, **kwargs: security.inspect_contract(
            contract, "base", http_get=lambda *a, **kw: Mock404Response()
        ),
    )

    asyncio.run(commands.inspect_command(update, ctx))

    assert len(msg.sent) == 2
    assert "🔎 Inspecting contract security…" in msg.sent[0]
    result_text = msg.sent[1]
    assert "🔎 Contract Inspection" in result_text
    assert "Chain: Base" in result_text
    assert "Risk: ⚪ Not Assessed" in result_text
    assert "Provider:\n• Honeypot.is has no assessment data for this contract." in result_text
    assert "No security assessment data available for this contract." not in result_text
    assert "Suspicious" not in result_text
    assert "High Risk" not in result_text
    assert "⚠️ Automated assessment. Not a guarantee of safety." in result_text

    # Verify persisted contract security record
    sec = checkpoint.get_contract_security("base", contract)
    assert sec is not None
    assert sec["risk_status"] == "not_assessed"
    assert sec["risk_reasons"] == []

