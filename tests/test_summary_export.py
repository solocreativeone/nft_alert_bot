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
    assert "Suspicious (1 collection)" in msg.sent[0]
    assert "1 signal" in msg.sent[0]
    assert "1. Example · Base" in msg.sent[0]


def test_sum_aggregates_multiple_signals_per_collection(monkeypatch):
    monkeypatch.setattr(commands, "CHAT_ID", "123")
    records = [
        # Credits: 3 signals, range -14.6% to +30.4%, current 0.03
        {"risk_status": "not_assessed", "collection": "Credits", "chain": "ethereum", "contract": "0x1111", "change_percent": -14.6, "current_floor": 0.025},
        {"risk_status": "not_assessed", "collection": "Credits", "chain": "ethereum", "contract": "0x1111", "change_percent": 10.0, "current_floor": 0.028},
        {"risk_status": "not_assessed", "collection": "Credits", "chain": "ethereum", "contract": "0x1111", "change_percent": 30.4, "current_floor": 0.03},
        # Misfits: 2 signals, range -22.2% to +28.6%, current 0.0009
        {"risk_status": "not_assessed", "collection": "Misfits NFT", "chain": "robinhood", "contract": "0x2222", "change_percent": -22.2, "current_floor": 0.0007},
        {"risk_status": "not_assessed", "collection": "Misfits NFT", "chain": "robinhood", "contract": "0x2222", "change_percent": 28.6, "current_floor": 0.0009},
        # Single signal collection
        {"risk_status": "not_assessed", "collection": "CREDITED PUNKS", "chain": "ethereum", "contract": "0x3333", "change_percent": -95.0, "current_floor": 0.05},
    ]
    monkeypatch.setattr(commands, "query_alert_history", lambda period: records)
    u, msg = update()
    asyncio.run(commands.sum_command(u, types.SimpleNamespace(args=["6h"])))

    text = msg.sent[0]
    assert "⚪ Not Assessed (3 collections)" in text
    assert "6 signals" in text
    assert "1. Credits · Ethereum\n   3 signals\n   Range: -14.6% → +30.4%\n   Current: 0.03 ETH" in text
    assert "2. Misfits NFT · Robinhood\n   2 signals\n   Range: -22.2% → +28.6%\n   Current: 0.0009 ETH" in text
    assert "3. CREDITED PUNKS · Ethereum\n   1 signal\n   -95.0%\n   Current: 0.05 ETH" in text


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
