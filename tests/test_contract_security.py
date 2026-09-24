import asyncio
import json
from datetime import datetime, timedelta, timezone
import types

import alert_history
import checkpoint
import commands


class MockMessage:
    def __init__(self):
        self.sent = []
        self.edited = []

    async def reply_text(self, text, **kwargs):
        msg = MockSentMessage(text, self)
        self.sent.append(text)
        return msg


class MockSentMessage:
    def __init__(self, text, parent):
        self.text = text
        self.parent = parent

    async def edit_text(self, text, **kwargs):
        self.text = text
        self.parent.edited.append(text)
        return self


def fake_update(chat_id="123"):
    msg = MockMessage()
    upd = types.SimpleNamespace(
        effective_chat=types.SimpleNamespace(id=chat_id),
        message=msg,
    )
    return upd, msg


def test_contract_security_survives_checkpoint_reload(tmp_path):
    path = str(tmp_path / "state.json")
    checkpoint.use_path(path)

    contract = "0x1111222233334444555566667777888899990000"
    checkpoint.set_contract_security("ethereum", contract, "suspicious", ["is_blacklisted"], {"verified": True})

    checkpoint.load(force=True)
    sec = checkpoint.get_contract_security("ethereum", contract)

    assert sec is not None
    assert sec["risk_status"] == "suspicious"
    assert sec["risk_reasons"] == ["is_blacklisted"]
    assert sec["risk_details"] == {"verified": True}
    assert isinstance(sec["updated_at"], str)


def test_inspect_stores_contract_level_security(monkeypatch, tmp_path):
    path = str(tmp_path / "state.json")
    checkpoint.use_path(path)
    monkeypatch.setattr(commands, "CHAT_ID", "123")

    contract = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    fake_result = {
        "contract": contract,
        "chain": "arbitrum",
        "risk": "looks_legit",
        "flags": ["clean"],
        "details": {"verified": True},
    }
    monkeypatch.setattr(commands, "inspect_security_contract", lambda *args, **kwargs: fake_result)

    update, msg = fake_update("123")
    ctx = types.SimpleNamespace(args=[contract, "arbitrum"])

    asyncio.run(commands.inspect_command(update, ctx))

    sec = checkpoint.get_contract_security("arbitrum", contract)
    assert sec is not None
    assert sec["risk_status"] == "looks_legit"
    assert sec["risk_reasons"] == ["clean"]
    assert sec["risk_details"] == {"verified": True}


def test_new_alert_inherits_suspicious_status(tmp_path):
    path = str(tmp_path / "state.json")
    checkpoint.use_path(path)

    contract = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    checkpoint.set_contract_security("base", contract, "suspicious", ["high_tax"], {"tax": 25})

    record = alert_history.record_alert(
        "floor_signal",
        {"chain": "base", "contract": contract, "name": "SuspiciousToken"},
        current_floor=0.5,
    )

    assert record["risk_status"] == "suspicious"
    assert record["risk_reasons"] == ["high_tax"]
    assert record["risk_details"] == {"tax": 25}


def test_new_alert_inherits_looks_legit_status(tmp_path):
    path = str(tmp_path / "state.json")
    checkpoint.use_path(path)

    contract = "0xcccccccccccccccccccccccccccccccccccccccc"
    checkpoint.set_contract_security("base", contract, "looks_legit", [], {"verified": True})

    record = alert_history.record_alert(
        "floor_signal",
        {"chain": "base", "contract": contract, "name": "LegitToken"},
        current_floor=1.0,
    )

    assert record["risk_status"] == "looks_legit"
    assert record["risk_reasons"] == []
    assert record["risk_details"] == {"verified": True}


def test_new_alert_inherits_high_risk_status(tmp_path):
    path = str(tmp_path / "state.json")
    checkpoint.use_path(path)

    contract = "0xdddddddddddddddddddddddddddddddddddddddd"
    checkpoint.set_contract_security("base", contract, "high_risk", ["is_honeypot"], {"honeypot": True})

    record = alert_history.record_alert(
        "floor_signal",
        {"chain": "base", "contract": contract, "name": "HoneypotToken"},
        current_floor=0.01,
    )

    assert record["risk_status"] == "high_risk"
    assert record["risk_reasons"] == ["is_honeypot"]
    assert record["risk_details"] == {"honeypot": True}


def test_unknown_contract_remains_not_assessed(tmp_path):
    path = str(tmp_path / "state.json")
    checkpoint.use_path(path)

    contract = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    record = alert_history.record_alert(
        "floor_signal",
        {"chain": "base", "contract": contract, "name": "UnknownToken"},
        current_floor=0.2,
    )

    assert record["risk_status"] == "not_assessed"
    assert record["risk_reasons"] == []
    assert "risk_details" not in record


def test_explicit_risk_status_overrides_inherited_status(tmp_path):
    path = str(tmp_path / "state.json")
    checkpoint.use_path(path)

    contract = "0xffffffffffffffffffffffffffffffffffffffff"
    checkpoint.set_contract_security("ethereum", contract, "suspicious", ["automated_flag"])

    record = alert_history.record_alert(
        "floor_signal",
        {"chain": "ethereum", "contract": contract, "name": "OverrideCol"},
        risk_status="looks_legit",
        risk_reasons=["manually_audited"],
    )

    assert record["risk_status"] == "looks_legit"
    assert record["risk_reasons"] == ["manually_audited"]


def test_sum_uses_contract_level_security(monkeypatch, tmp_path):
    path = str(tmp_path / "state.json")
    checkpoint.use_path(path)
    monkeypatch.setattr(commands, "CHAT_ID", "123")

    contract = "0x1234567890123456789012345678901234567890"
    checkpoint.set_contract_security("ethereum", contract, "suspicious")

    # Alerts initially stored as not_assessed
    records = [
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "floor_signal",
            "chain": "ethereum",
            "collection": "Credits",
            "contract": contract,
            "change_percent": 15.0,
            "current_floor": 0.5,
            "risk_status": "not_assessed",
        }
    ]
    monkeypatch.setattr(commands, "query_alert_history", lambda period: records)

    update, msg = fake_update("123")
    asyncio.run(commands.sum_command(update, types.SimpleNamespace(args=["24h"])))

    assert len(msg.sent) == 1
    text = msg.sent[0]
    assert "⚠️ Suspicious (1 collection)" in text
    assert "1. Credits · Ethereum" in text


def test_old_inspection_outside_sum_window_still_classifies_newer_alerts(tmp_path):
    path = str(tmp_path / "state.json")
    checkpoint.use_path(path)

    contract = "0xcredits000000000000000000000000000000000"
    now = datetime.now(timezone.utc)

    # 12h ago: /inspect stored suspicious status
    checkpoint.set_contract_security(
        "ethereum", contract, "suspicious",
        updated_at=(now - timedelta(hours=12)).isoformat(),
    )

    # 12h ago: alert was created and stored (older than 6h)
    checkpoint.append_alert({
        "timestamp": (now - timedelta(hours=12)).isoformat(),
        "type": "floor_signal",
        "chain": "ethereum",
        "collection": "Credits",
        "contract": contract,
        "current_floor": 0.3,
        "change_percent": 10.0,
        "risk_status": "suspicious",
    })

    # 2h ago: new alert arrives (inside 6h window)
    checkpoint.append_alert({
        "timestamp": (now - timedelta(hours=2)).isoformat(),
        "type": "floor_signal",
        "chain": "ethereum",
        "collection": "Credits",
        "contract": contract,
        "current_floor": 0.35,
        "change_percent": 16.7,
        "risk_status": "not_assessed",  # simulated legacy / unclassified event
    })

    # Query alert history for 6h window
    recent_alerts = alert_history.query_alert_history("6h", now=now)
    assert len(recent_alerts) == 1  # Only the 2h ago alert is within 6h

    # Aggregate alert history
    groups = alert_history.aggregate_alert_history(recent_alerts)
    assert len(groups["suspicious"]) == 1
    assert groups["suspicious"][0]["collection"] == "Credits"
    assert len(groups["not_assessed"]) == 0


def test_existing_state_json_without_contract_security_loads_safely(tmp_path):
    path = str(tmp_path / "state.json")
    legacy_payload = {
        "version": 1,
        "blocks": {"ethereum": 12345},
        "signatures": {},
        "seen": {},
        "gemini": {},
        "floors": {"ethereum:0xabc": 2.5},
        "alerts": [],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(legacy_payload, f)

    checkpoint.use_path(path)
    assert checkpoint.get_contract_security("ethereum", "0xabc") is None
    assert checkpoint.get_block("ethereum") == 12345
    assert checkpoint.get_floor("ethereum:0xabc") == 2.5

    # Should safely allow writing contract_security
    checkpoint.set_contract_security("ethereum", "0xabc", "looks_legit")
    sec = checkpoint.get_contract_security("ethereum", "0xabc")
    assert sec is not None
    assert sec["risk_status"] == "looks_legit"


def test_chain_and_contract_used_as_security_key(tmp_path):
    path = str(tmp_path / "state.json")
    checkpoint.use_path(path)

    contract_upper = "0xABCDEF1234567890ABCDEF1234567890ABCDEF12"
    checkpoint.set_contract_security("ETHEREUM", contract_upper, "suspicious")

    # Lowercase lookup
    sec = checkpoint.get_contract_security("ethereum", contract_upper.lower())
    assert sec is not None
    assert sec["risk_status"] == "suspicious"

    # Loaded raw state key check
    raw_keys = checkpoint.load().get("contract_security", {})
    expected_key = f"ethereum:{contract_upper.lower()}"
    assert expected_key in raw_keys


def test_same_contract_different_chains_do_not_share_risk_status(tmp_path):
    path = str(tmp_path / "state.json")
    checkpoint.use_path(path)

    contract = "0x9999999999999999999999999999999999999999"
    checkpoint.set_contract_security("ethereum", contract, "high_risk")
    checkpoint.set_contract_security("polygon", contract, "looks_legit")

    sec_eth = checkpoint.get_contract_security("ethereum", contract)
    sec_poly = checkpoint.get_contract_security("polygon", contract)

    assert sec_eth["risk_status"] == "high_risk"
    assert sec_poly["risk_status"] == "looks_legit"
