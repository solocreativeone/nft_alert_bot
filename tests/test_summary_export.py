import asyncio
import types

import alert_history
import commands


class Message:
    def __init__(self):
        self.sent = []
        self.documents = []

    async def reply_text(self, text, **kwargs):
        self.sent.append(text)

    async def reply_document(self, **kwargs):
        self.documents.append(kwargs)


def update(chat_id="123"):
    message = Message()
    return types.SimpleNamespace(effective_chat=types.SimpleNamespace(id=chat_id), message=message), message


def test_sum_groups_without_calling_security(monkeypatch):
    monkeypatch.setattr(commands, "CHAT_ID", "123")
    monkeypatch.setattr(commands, "query_alert_history", lambda period: [
        {"risk_status": "suspicious", "collection": "Example", "chain": "base", "change_percent": 10.0}
    ])
    monkeypatch.setattr(commands, "inspect_security_contract", lambda *a: (_ for _ in ()).throw(AssertionError()))
    u, msg = update()
    asyncio.run(commands.sum_command(u, types.SimpleNamespace(args=["24h"])))
    assert "Suspicious (1)" in msg.sent[0]


def test_export_denied_and_formats(monkeypatch):
    monkeypatch.setattr(commands, "CHAT_ID", "123")
    monkeypatch.setattr(alert_history, "can_export", lambda user_id: False)
    u, msg = update()
    asyncio.run(commands.export_command(u, types.SimpleNamespace(args=[])))
    assert "not available" in msg.sent[0]

    monkeypatch.setattr(alert_history, "can_export", lambda user_id: True)
    monkeypatch.setattr(commands, "can_export", lambda user_id: True)
    monkeypatch.setattr(commands, "query_alert_history", lambda period: [{"timestamp": "x"}])
    u, msg = update()
    asyncio.run(commands.export_command(u, types.SimpleNamespace(args=["6h", "json"])))
    assert msg.documents[0]["filename"].endswith(".json")
