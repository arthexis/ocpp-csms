"""Pure, read-only classification of exceptional stored OCPP evidence.

This module intentionally does not send notifications or change charge authorization.
Ordinary StartTransaction evidence is not an alert; later TOML mail rules may
subscribe to it independently.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

ALERT_SCHEMA = "ocpp-csms/alert/v1"

# These are noteworthy runtime transitions, not necessarily faults.
_RUNTIME_RULES: dict[str, tuple[str, str, str]] = {
    "charger_disconnected": ("warning", "connectivity", "Charger disconnected"),
    "charger_connected": ("info", "connectivity", "Charger connected"),
}

_FAULT_STATUSES = {"Faulted"}
_REJECTED_AUTH = {"Blocked", "Invalid", "Expired", "ConcurrentTx"}


def _details(row: Mapping[str, Any]) -> dict[str, Any]:
    value = row.get("payload")
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        decoded = json.loads(value or "{}")
    except (ValueError, TypeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def classify_event(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return one alert for an exceptional event, or None for routine evidence.

    The output carries its source event ID; unrelated OCPP messages are never
    correlated based on adjacency. The rule list is conservative by design.
    """
    row = dict(row)  # sqlite3.Row exposes keys but not Mapping.get\n    action = str(row.get("action") or "")
    kind = str(row.get("kind") or "")
    payload = _details(row)
    severity: str
    category: str
    message: str

    if kind == "runtime":
        rule = _RUNTIME_RULES.get(action)
        if rule is None:
            return None
        severity, category, message = rule
    elif kind == "ocpp" and action == "StatusNotification" and row.get("direction") == "in":
        status = str(payload.get("status") or "")
        error = str(payload.get("error_code") or "")
        info = str(payload.get("info") or "")
        if status == "Faulted" and error == "InternalError" and info == "EmergencyStop":
            severity, category, message = "error", "emergency_stop", "Emergency stop detected"
        elif status in _FAULT_STATUSES or error not in {"", "NoError"}:
            severity, category, message = "error", "charger_fault", "Charger reported a fault"
        else:
            return None
    elif kind == "ocpp" and action in {"Authorize", "StartTransaction"} and row.get("direction") == "out":
        tag_info = payload.get("idTagInfo")
        status = tag_info.get("status") if isinstance(tag_info, dict) else payload.get("status")
        if status not in _REJECTED_AUTH:
            return None
        severity, category, message = "warning", "authorization", f"Authorization {status}"
    else:
        return None

    details = {
        key: payload[key]
        for key in ("status", "error_code", "info", "vendor_id", "vendor_error_code",
                    "reason", "connector_id")
        if payload.get(key) not in (None, "")
    }
    record: dict[str, Any] = {
        "source_event_id": row.get("id"),
        "at": row.get("occurred_at"),
        "charger_id": row.get("charger_id"),
        "severity": severity,
        "category": category,
        "message": message,
        "action": action,
        "details": details,
    }
    if row.get("transaction_id") is not None:
        record["transaction_id"] = row["transaction_id"]
    if row.get("id_tag") is not None:
        record["rfid"] = row["id_tag"]
    if payload.get("connector_id") is not None:
        record["connector_id"] = payload["connector_id"]
    return record


def classify_events(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Retain every qualifying occurrence in source order, without grouping."""
    return [alert for row in rows if (alert := classify_event(row)) is not None]
