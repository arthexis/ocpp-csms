from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable

from ocpp_csms.output import json_command_result

SCHEMA = "ocpp-csms/events/v1"
RAW_SCHEMA = "ocpp-csms/events-raw/v1"


def _payload(row: sqlite3.Row) -> dict[str, Any]:
    try:
        value = json.loads(row["payload"] or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _connector_id(payload: dict[str, Any]) -> int | None:
    value = payload.get("connector_id")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def event_record(row: sqlite3.Row) -> dict[str, object]:
    payload = _payload(row)
    action = str(row["action"])
    base: dict[str, object] = {
        "id": int(row["id"]),
        "at": row["occurred_at"],
        "charger_id": row["charger_id"],
        "kind": "ocpp" if row["kind"] == "ocpp" else action,
        "action": action,
    }

    if row["kind"] == "runtime":
        if action == "charger_connected":
            base["kind"] = "charger_connected"
            base["protocol"] = payload.get("subprotocol")
        elif action == "charger_disconnected":
            base["kind"] = "charger_disconnected"
        return base

    connector_id = _connector_id(payload)
    if connector_id is not None:
        base["connector_id"] = connector_id
    if row["transaction_id"] is not None:
        base["transaction_id"] = int(row["transaction_id"])
    if row["id_tag"] is not None:
        base["rfid"] = str(row["id_tag"])

    if action == "StatusNotification":
        base["kind"] = "status"
        base["status"] = payload.get("status")
        error = payload.get("error_code")
        base["error_code"] = None if error in {None, "NoError"} else error
        base["info"] = payload.get("info")
        base["derived_status"] = ("EmergencyStop" if payload.get("status") == "Faulted" and error == "InternalError" and payload.get("info") == "EmergencyStop" else payload.get("status"))
    elif action == "StartTransaction":
        base["kind"] = "transaction_started"
    elif action == "StopTransaction":
        base["kind"] = "transaction_stopped"
    elif action == "Authorize":
        base["kind"] = "authorization"
    elif action == "BootNotification":
        base["kind"] = "boot"
    elif action == "MeterValues":
        base["kind"] = "meter_values"

    return base


def events_contract(rows: Iterable[sqlite3.Row]) -> dict[str, object]:
    return json_command_result(
        {"events": [event_record(row) for row in rows]},
        schema=SCHEMA,
    )


def raw_events_contract(rows: Iterable[sqlite3.Row]) -> dict[str, object]:
    records = []
    for row in rows:
        records.append(
            {
                "id": int(row["id"]),
                "at": row["occurred_at"],
                "charger_id": row["charger_id"],
                "kind": row["kind"],
                "action": row["action"],
                "direction": row["direction"],
                "transaction_id": int(row["transaction_id"]) if row["transaction_id"] is not None else None,
                "rfid": row["id_tag"],
                "payload": _payload(row),
            }
        )
    return json_command_result({"events": records}, schema=RAW_SCHEMA)
