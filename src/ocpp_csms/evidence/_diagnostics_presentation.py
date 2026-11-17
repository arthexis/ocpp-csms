"""Pure human-readable fault and event rendering; SQLite queries stay in diagnostics."""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from typing import Any


def render_faults(faults: list[dict[str, Any]], *, event_display_timestamp: Callable[..., str], local_time: bool = False) -> str:
    if not faults:
        return "No recorded fault notifications."
    lines = []
    for fault in faults:
        connector = fault["connector_id"]
        parts = [event_display_timestamp(str(fault["received_at"]), local_time=local_time), str(fault["charger_id"]),
                 f"C{connector}" if connector is not None else "C?",
                 "Faulted", str(fault.get("error_code") or "UnknownError")]
        for name in ("charger_at", "info", "vendor_id", "vendor_error_code"):
            if fault.get(name) not in (None, ""):
                parts.append(f"{name}={event_display_timestamp(str(fault[name]), local_time=local_time) if name == 'charger_at' else fault[name]}")
        lines.append("  ".join(parts))
    return "\n".join(lines)


def render_summary(row: sqlite3.Row, *, detail: bool = False) -> str:
    """Summarize one stored event without assuming frame correlation."""
    if row["kind"] == "runtime":
        label = str(row["action"]).replace("_", " ")
        if not detail or row["action"] != "charger_connected":
            return label
        try:
            metadata = json.loads(row["payload"] or "{}")
        except (TypeError, ValueError):
            return label
        if not isinstance(metadata, dict):
            return label
        fields = ("path", "subprotocol", "remote", "user_agent", "requested_subprotocols")
        return " ".join([label, *(f"{key}={json.dumps(metadata[key], ensure_ascii=False)}"
                                  for key in fields if metadata.get(key) is not None)])

    try:
        payload = json.loads(row["payload"] or "{}")
    except (TypeError, ValueError):
        return f"{row['action']} [invalid payload]"
    if not isinstance(payload, dict):
        return f"{row['action']} [unexpected payload]"

    action = str(row["action"])
    direction = row["direction"]
    details = []
    connector = payload.get("connector_id")
    if connector is not None:
        details.append(f"C{connector}")
    if row["id_tag"]:
        details.append(f"RFID {row['id_tag']}")
    if row["transaction_id"] is not None:
        details.append(f"tx={row['transaction_id']}")

    if action == "BootNotification":
        if direction == "in":
            # Boot identity is valuable diagnostic evidence; the compact view
            # keeps the standard OCPP action name without inventing a boot event.
            if detail:
                for key in ("charge_point_vendor", "charge_point_model",
                            "charge_point_serial_number", "charge_box_serial_number",
                            "firmware_version", "iccid", "imsi", "meter_type",
                            "meter_serial_number"):
                    if payload.get(key) is not None:
                        details.append(f"{key}={payload[key]}")
            return " ".join([action, *details])
        if direction == "out":
            for key in ("status", "interval"):
                if payload.get(key) is not None:
                    details.append(f"{key}={payload[key]}")
            if detail and (payload.get("current_time") or payload.get("currentTime")):
                details.append(f"current_time={payload.get('current_time') or payload.get('currentTime')}")
            return "→ " + " ".join([action, *(details or ["response"])])

    if action == "StatusNotification":
        details.extend(str(value) for value in (
            payload.get("status"), payload.get("error_code"), payload.get("info")
        ) if value is not None and value != "" and value != "NoError")
        if detail:
            for key in ("vendor_id", "vendor_error_code", "timestamp"):
                if payload.get(key) not in (None, ""):
                    details.append(f"{key}={payload[key]}")
    elif direction == "out":
        status = payload.get("status")
        info = payload.get("idTagInfo")
        if status is None and isinstance(info, dict):
            status = info.get("status")
        if status is not None:
            details.append(str(status))
        if payload.get("transaction_id") is not None and row["transaction_id"] is None:
            details.append(f"tx={payload['transaction_id']}")
        if action == "Heartbeat" and (payload.get("currentTime") is not None or payload.get("current_time") is not None):
            details.append("response")
        if not details:
            details.append("response")
        # Keep unexpected response details visible rather than presenting an
        # unfamiliar payload as a routine acknowledgement.
        known = {"status", "idTagInfo", "transaction_id", "current_time", "currentTime"}
        extras = {k: v for k, v in payload.items() if k not in known}
        if extras:
            details.append(f"extra={json.dumps(extras, sort_keys=True, ensure_ascii=False) if detail else len(extras)}")
        return "→ " + " ".join([action, *details])
    elif action in {"StartTransaction", "StopTransaction"}:
        for key in ("reason", "meter_start", "meter_stop"):
            if payload.get(key) is not None:
                details.append(f"{key}={payload[key]}")

    return " ".join([action, *details])
