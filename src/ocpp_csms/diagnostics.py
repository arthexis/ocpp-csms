from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


@dataclass
class DiagnosticEvent:
    occurred_at: str
    kind: str
    charger_id: str | None
    action: str
    direction: str | None = None
    transaction_id: int | None = None
    id_tag: str | None = None
    payload: dict[str, Any] | None = None


def _connect(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    return connection


def _parse_time(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def events_between(
    data_dir: str | Path,
    *,
    charger_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 200,
) -> list[DiagnosticEvent]:
    database = Path(data_dir).expanduser() / "events.sqlite3"
    if not database.exists():
        return []

    conditions = []
    params: list[Any] = []
    if charger_id:
        conditions.append("charger_id = ?")
        params.append(charger_id)
    if since:
        conditions.append("occurred_at >= ?")
        params.append(_iso(_parse_time(since)))
    if until:
        conditions.append("occurred_at <= ?")
        params.append(_iso(_parse_time(until)))
    where = " WHERE " + " AND ".join(conditions) if conditions else ""

    event_conditions = []
    event_params: list[Any] = []
    if charger_id:
        event_conditions.append("charger_id = ?")
        event_params.append(charger_id)
    if since:
        event_conditions.append("received_at >= ?")
        event_params.append(_iso(_parse_time(since)))
    if until:
        event_conditions.append("received_at <= ?")
        event_params.append(_iso(_parse_time(until)))
    event_where = " WHERE " + " AND ".join(event_conditions) if event_conditions else ""

    with _connect(database) as connection:
        runtime_rows = connection.execute(
            f"""
            SELECT occurred_at, charger_id, event AS action, details_json
            FROM runtime_events
            {where}
            ORDER BY occurred_at DESC, id DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        ocpp_rows = connection.execute(
            f"""
            SELECT received_at AS occurred_at, charger_id, action, direction,
                   transaction_id, id_tag, payload_json
            FROM events
            {event_where}
            ORDER BY received_at DESC, id DESC
            LIMIT ?
            """,
            (*event_params, limit),
        ).fetchall()

    events: list[DiagnosticEvent] = []
    for row in runtime_rows:
        details = json.loads(row["details_json"]) if row["details_json"] else None
        events.append(
            DiagnosticEvent(
                occurred_at=row["occurred_at"],
                kind="runtime",
                charger_id=row["charger_id"],
                action=row["action"],
                payload=details,
            )
        )
    for row in ocpp_rows:
        events.append(
            DiagnosticEvent(
                occurred_at=row["occurred_at"],
                kind="ocpp",
                charger_id=row["charger_id"],
                action=row["action"],
                direction=row["direction"],
                transaction_id=row["transaction_id"],
                id_tag=row["id_tag"],
                payload=json.loads(row["payload_json"]),
            )
        )

    events.sort(key=lambda item: item.occurred_at)
    return events[-limit:]


def events_around(
    data_dir: str | Path,
    charger_id: str,
    at: str,
    *,
    minutes: int = 10,
) -> list[DiagnosticEvent]:
    center = _parse_time(at)
    return events_between(
        data_dir,
        charger_id=charger_id,
        since=_iso(center - timedelta(minutes=minutes)),
        until=_iso(center + timedelta(minutes=minutes)),
        limit=500,
    )


def _response_status(payload: dict[str, Any]) -> str | None:
    direct = payload.get("status")
    if direct is not None:
        return str(direct)
    info = payload.get("idTagInfo")
    if isinstance(info, dict) and info.get("status") is not None:
        return str(info["status"])
    return None


def _summary(event: DiagnosticEvent) -> str:
    if event.kind == "runtime":
        return event.action.replace("_", " ")

    payload = event.payload or {}
    prefix = "→ " if event.direction == "out" else ""
    if event.direction == "out":
        status = _response_status(payload)
        suffix = f" {status}" if status else " response"
        if event.action == "StartTransaction" and event.transaction_id is not None:
            suffix += f" tx={event.transaction_id}"
        return f"{prefix}{event.action}{suffix}"

    if event.action == "Authorize":
        return f"Authorize RFID {event.id_tag or payload.get('id_tag') or '-'}"
    if event.action == "StartTransaction":
        suffix = f" tx={event.transaction_id}" if event.transaction_id is not None else ""
        return f"StartTransaction{suffix} RFID {event.id_tag or payload.get('id_tag') or '-'}"
    if event.action == "StopTransaction":
        return f"StopTransaction tx={event.transaction_id if event.transaction_id is not None else '-'}"
    if event.action == "StatusNotification":
        status = payload.get("status") or "Unknown"
        error = payload.get("error_code")
        if error and error != "NoError":
            return f"StatusNotification {status} / {error}"
        return f"StatusNotification {status}"
    if event.action == "MeterValues":
        return f"MeterValues tx={event.transaction_id if event.transaction_id is not None else '-'}"
    return event.action


def format_events(events: list[DiagnosticEvent]) -> str:
    if not events:
        return "No matching events."
    lines = []
    for event in events:
        charger = f" {event.charger_id}" if event.charger_id else ""
        lines.append(f"{event.occurred_at}{charger}  {_summary(event)}")
    return "\n".join(lines)


def _format_explanation(charger_id: str, heading: str, events: list[DiagnosticEvent]) -> str:
    if not events:
        return f"{charger_id}: no recorded evidence {heading}"

    lines = [f"{charger_id} {heading}", ""]
    for event in events:
        lines.append(f"{event.occurred_at}  {_summary(event)}")

    inbound = [event for event in events if event.kind == "ocpp" and event.direction != "out"]
    outbound = [event for event in events if event.kind == "ocpp" and event.direction == "out"]
    inbound_actions = [event.action for event in inbound]
    runtime_actions = [event.action for event in events if event.kind == "runtime"]
    status_events = [event for event in inbound if event.action == "StatusNotification"]
    fault = next(
        (
            event
            for event in reversed(status_events)
            if (event.payload or {}).get("status") == "Faulted"
            or ((event.payload or {}).get("error_code") not in (None, "NoError"))
        ),
        None,
    )

    lines.append("")
    lines.append("Evidence summary:")
    if "Authorize" in inbound_actions:
        lines.append("- Authorization request was received by the CSMS.")
        authorize_reply = next((event for event in outbound if event.action == "Authorize"), None)
        if authorize_reply is not None:
            status = _response_status(authorize_reply.payload or {})
            lines.append(f"- CSMS authorization reply: {status or 'recorded response'}.")
    if "StartTransaction" in inbound_actions:
        lines.append("- A StartTransaction was received.")
        start_reply = next((event for event in outbound if event.action == "StartTransaction"), None)
        if start_reply is not None:
            status = _response_status(start_reply.payload or {})
            lines.append(f"- CSMS StartTransaction reply: {status or 'recorded response'}.")
    else:
        lines.append("- No StartTransaction was recorded in this window.")
    if fault is not None:
        payload = fault.payload or {}
        lines.append(
            f"- Charger reported {payload.get('status', 'Faulted')}"
            + (f" / {payload.get('error_code')}" if payload.get("error_code") else "")
            + "."
        )
    if "charger_disconnected" in runtime_actions:
        lines.append("- Charger disconnected during this window.")
    if "StopTransaction" in inbound_actions:
        lines.append("- A StopTransaction was received.")

    return "\n".join(lines)


def explain(data_dir: str | Path, charger_id: str, at: str, *, minutes: int = 10) -> str:
    events = events_around(data_dir, charger_id, at, minutes=minutes)
    return _format_explanation(charger_id, f"around {at}", events)


def explain_between(
    data_dir: str | Path,
    charger_id: str,
    since: str,
    until: str,
) -> str:
    if _parse_time(since) > _parse_time(until):
        raise ValueError("--since must be earlier than or equal to --until")
    events = events_between(
        data_dir,
        charger_id=charger_id,
        since=since,
        until=until,
        limit=1000,
    )
    return _format_explanation(charger_id, f"from {since} until {until}", events)
