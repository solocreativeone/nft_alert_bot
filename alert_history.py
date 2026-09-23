"""Structured, reusable alert-history queries and exports."""
import csv
import io
import json
import os
from datetime import datetime, timedelta, timezone

import checkpoint

RISK_STATUSES = ("looks_legit", "suspicious", "high_risk", "not_assessed")
RISK_LABELS = {
    "looks_legit": "🟢 Looks Legit",
    "suspicious": "⚠️ Suspicious",
    "high_risk": "🔴 High Risk",
    "not_assessed": "⚪ Not Assessed",
}
EXPORT_FIELDS = (
    "timestamp", "type", "chain", "collection", "contract",
    "previous_floor", "current_floor", "change_percent", "risk_status",
)


def record_alert(alert_type, collection, previous_floor=None, current_floor=None,
                 change_percent=None, risk_status=None, risk_reasons=None, context=None):
    """Build and persist one alert record after delivery has succeeded."""
    timestamp = datetime.now(timezone.utc).isoformat()
    record = {
        "timestamp": timestamp,
        "type": alert_type,
        "chain": (collection.get("chain") or "ethereum").lower(),
        "collection": collection.get("name") or "Unknown",
        "contract": collection.get("contract"),
        "previous_floor": previous_floor,
        "current_floor": current_floor,
        "change_percent": change_percent,
        "risk_status": normalize_risk_status(risk_status),
        "risk_reasons": list(risk_reasons or []),
    }
    if context:
        record["context"] = dict(context)
    checkpoint.append_alert(record, flush_now=True)
    return record


def normalize_risk_status(value):
    return value if value in RISK_STATUSES else "not_assessed"


def parse_period(value, now=None):
    """Return a UTC [start, end) window for a compact user period."""
    now = now or datetime.now(timezone.utc)
    text = (value or "24h").strip().lower()
    if text == "all":
        return datetime(1970, 1, 1, tzinfo=timezone.utc), now
    if text == "yesterday":
        end = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return end - timedelta(days=1), end
    if text.endswith("h"):
        try:
            hours = float(text[:-1])
        except ValueError:
            hours = 24
        if hours > 0:
            return now - timedelta(hours=hours), now
    return now - timedelta(hours=24), now


def _record_timestamp(record):
    try:
        return datetime.fromisoformat(str(record.get("timestamp")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def query_alert_history(period=None, now=None):
    start, end = parse_period(period, now=now)
    result = []
    for record in checkpoint.get_alerts():
        timestamp = _record_timestamp(record)
        if timestamp is None:
            continue
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        if start <= timestamp < end:
            item = dict(record)
            item["risk_status"] = normalize_risk_status(item.get("risk_status"))
            result.append(item)
    return result


def summarize_alert_history(records):
    groups = {status: [] for status in RISK_STATUSES}
    for record in records:
        groups[normalize_risk_status(record.get("risk_status"))].append(record)
    return groups


def aggregate_alert_history(records):
    """Aggregate alert events by unique collection within risk statuses."""
    col_records = {}
    for record in records:
        chain = (record.get("chain") or "ethereum").lower()
        contract = record.get("contract")
        contract_key = contract.lower() if contract else None
        name = record.get("collection") or "Unknown"
        key = (chain, contract_key if contract_key else name.lower())
        if key not in col_records:
            col_records[key] = []
        col_records[key].append(record)

    groups = {status: [] for status in RISK_STATUSES}
    for key, items in col_records.items():
        latest = items[-1]
        chain = items[-1].get("chain") or "ethereum"
        name = items[-1].get("collection") or "Unknown"
        contract = items[-1].get("contract")
        status = normalize_risk_status(items[-1].get("risk_status"))

        changes = [float(r["change_percent"]) for r in items if r.get("change_percent") is not None]
        floors = [r["current_floor"] for r in items if isinstance(r.get("current_floor"), (int, float))]
        prev_floors = [r["previous_floor"] for r in items if isinstance(r.get("previous_floor"), (int, float))]

        col_data = {
            "collection": name,
            "chain": chain,
            "contract": contract,
            "risk_status": status,
            "signal_count": len(items),
            "min_change": min(changes) if changes else None,
            "max_change": max(changes) if changes else None,
            "single_change": changes[0] if len(changes) == 1 else None,
            "current_floor": floors[-1] if floors else None,
            "previous_floor": prev_floors[0] if prev_floors else None,
            "type": latest.get("type", "alert"),
        }
        groups[status].append(col_data)

    for status in RISK_STATUSES:
        groups[status].sort(key=lambda x: x["signal_count"], reverse=True)

    return groups


def export_alert_history_csv(records):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=EXPORT_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for record in records:
        writer.writerow({field: record.get(field) for field in EXPORT_FIELDS})
    return output.getvalue().encode("utf-8")


def export_alert_history_json(records):
    return json.dumps(list(records), ensure_ascii=False, indent=2, default=str).encode("utf-8")


def can_export(user_id):
    """Single access seam; current deployment permits configured Telegram chats."""
    configured = os.environ.get("CHAT_ID")
    if configured is None:
        try:
            from config import CHAT_ID
            configured = CHAT_ID
        except ImportError:
            configured = ""
    return str(user_id) in {item.strip() for item in str(configured or "").split(",") if item.strip()}
