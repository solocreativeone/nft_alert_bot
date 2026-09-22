from datetime import datetime, timedelta, timezone

import alert_history
import checkpoint


def _record(hours_ago=1, **extra):
    row = {
        "timestamp": (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat(),
        "type": "floor_signal", "chain": "ethereum", "collection": "Example",
        "contract": "0x" + "a" * 40, "previous_floor": 1.0,
        "current_floor": 1.2, "change_percent": 20.0,
    }
    row.update(extra)
    return row


def test_alert_persists_and_restart_loads_it():
    checkpoint.append_alert(_record(), flush_now=True)
    checkpoint.load(force=True)
    assert checkpoint.get_alerts()[0]["collection"] == "Example"


def test_query_periods_and_missing_risk_status():
    checkpoint.append_alert(_record(hours_ago=2), flush_now=False)
    checkpoint.append_alert(_record(hours_ago=26, collection="Yesterday"), flush_now=False)
    assert len(alert_history.query_alert_history("6h")) == 1
    assert len(alert_history.query_alert_history("24h")) == 1
    assert len(alert_history.query_alert_history("yesterday")) == 1
    assert alert_history.query_alert_history("6h")[0]["risk_status"] == "not_assessed"


def test_exports_share_structured_records():
    record = _record(risk_status="suspicious")
    csv_text = alert_history.export_alert_history_csv([record]).decode()
    json_text = alert_history.export_alert_history_json([record]).decode()
    assert "timestamp,type,chain,collection,contract" in csv_text
    assert "Telegram" not in json_text
    assert '"risk_status": "suspicious"' in json_text
