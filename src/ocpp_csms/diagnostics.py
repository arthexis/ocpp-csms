from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ocpp_csms.events import DATABASE_FILENAME


def _time(value: str) -> str:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def events_between(
    data_dir: str | Path,
    *,
    charger_id: str | None = None,
    transaction_id: int | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 200,
) -> list[sqlite3.Row]:
    since = _time(since) if since else None
    until = _time(until) if until else None
    if since and until and since > until:
        raise ValueError("--since must be earlier than or equal to --until")

    database = Path(data_dir).expanduser() / DATABASE_FILENAME
    if not database.exists():
        return []

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT * FROM (
                SELECT id, received_at AS occurred_at, charger_id, 'ocpp' AS kind,
                       action, direction, transaction_id, id_tag, payload_json AS payload
                FROM events
                UNION ALL
                SELECT id, occurred_at, charger_id, 'runtime', event, NULL, NULL, NULL, details_json
                FROM runtime_events
            )
            WHERE (? IS NULL OR charger_id = ?)
              AND (? IS NULL OR transaction_id = ?)
              AND (? IS NULL OR occurred_at >= ?)
              AND (? IS NULL OR occurred_at <= ?)
            ORDER BY occurred_at DESC, id DESC
            LIMIT ?
            """,
            (
                charger_id,
                charger_id,
                transaction_id,
                transaction_id,
                since,
                since,
                until,
                until,
                limit,
            ),
        ).fetchall()
    finally:
        connection.close()
    rows.reverse()
    return rows


def transaction_events(
    data_dir: str | Path,
    transaction_id: int,
    *,
    limit: int = 1000,
) -> list[sqlite3.Row]:
    """Return OCPP evidence explicitly associated with one transaction."""
    database = Path(data_dir).expanduser() / DATABASE_FILENAME
    if not database.exists():
        return []

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            """
            SELECT id, received_at AS occurred_at, charger_id, 'ocpp' AS kind,
                   action, direction, transaction_id, id_tag, payload_json AS payload
            FROM events
            WHERE transaction_id = ?
            ORDER BY occurred_at ASC, id ASC
            LIMIT ?
            """,
            (int(transaction_id), limit),
        ).fetchall()
    finally:
        connection.close()


def _summary(row: sqlite3.Row) -> str:
    """Summarize one stored event without assuming frame correlation."""
    if row["kind"] == "runtime":
        return str(row["action"]).replace("_", " ")

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

    if action == "StatusNotification":
        details.extend(str(value) for value in (
            payload.get("status"), payload.get("error_code"), payload.get("info")
        ) if value is not None and value != "" and value != "NoError")
    elif direction == "out":
        status = payload.get("status")
        info = payload.get("idTagInfo")
        if status is None and isinstance(info, dict):
            status = info.get("status")
        if status is not None:
            details.append(str(status))
        if payload.get("transaction_id") is not None and row["transaction_id"] is None:
            details.append(f"tx={payload['transaction_id']}")
        if payload.get("current_time") is not None and action == "Heartbeat":
            details.append("ack")
        if not details:
            details.append("response")
        return "→ " + " ".join([action, *details])
    elif action in {"StartTransaction", "StopTransaction"}:
        for key in ("reason", "meter_start", "meter_stop"):
            if payload.get(key) is not None:
                details.append(f"{key}={payload[key]}")

    return " ".join([action, *details])


def _group_key(row: sqlite3.Row) -> tuple[object, ...] | None:
    """Only collapse adjacent, independently identifiable routine records.

    In particular, stored events have no OCPP message ID: requests and
    responses are never paired, even when adjacent.
    """
    if row["kind"] != "ocpp" or row["transaction_id"] is not None or row["id_tag"] is not None:
        return None
    try:
        payload = json.loads(row["payload"] or "{}")
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    action, direction = row["action"], row["direction"]
    if action == "Heartbeat":
        if direction == "in" and not payload:
            return ("heartbeat-request", row["charger_id"])
        if (direction == "out" and set(payload) == {"current_time"}
                and isinstance(payload["current_time"], str) and payload["current_time"]):
            return ("heartbeat-response", row["charger_id"])
        return None
    if action == "StatusNotification" and direction == "in":
        status = payload.get("status")
        error = payload.get("error_code")
        info = payload.get("info")
        if status not in {"Available", "Preparing", "Charging", "SuspendedEV",
                          "SuspendedEVSE", "Finishing", "Reserved", "Unavailable"}:
            return None
        if error not in (None, "NoError") or info not in (None, ""):
            return None
        connector = payload.get("connector_id")
        if not isinstance(connector, int) or isinstance(connector, bool):
            return None
        # Unexpected fields may carry important evidence; do not collapse them.
        if set(payload) - {"connector_id", "status", "error_code", "info",
                            "timestamp", "vendor_id", "vendor_error_code"}:
            return None
        if payload.get("vendor_id") or payload.get("vendor_error_code"):
            return None
        return ("status", row["charger_id"], connector, status, error, info)
    return None


def format_events(rows: list[sqlite3.Row], heading: str | None = None) -> str:
    if not rows:
        return "No matching events."
    lines = [heading, ""] if heading else []

    def append_group(group: list[sqlite3.Row]) -> None:
        first, last = group[0], group[-1]
        charger = f" {first['charger_id']}" if first["charger_id"] else ""
        summary = _summary(first)
        if len(group) == 1:
            lines.append(f"{first['occurred_at']}{charger}  {summary}")
        else:
            # These are counts of observed stored events, not inferred
            # request/response pairs or claims about unseen window edges.
            lines.append(
                f"{first['occurred_at']}–{last['occurred_at']}{charger}  "
                f"{summary} ×{len(group)}"
            )

    group: list[sqlite3.Row] = []
    key: tuple[object, ...] | None = None
    for row in rows:
        candidate = _group_key(row)
        if group and (candidate is None or candidate != key):
            append_group(group)
            group = []
        group.append(row)
        key = candidate
        if candidate is None:
            append_group(group)
            group = []
            key = None
    if group:
        append_group(group)
    return "\n".join(lines)


def explain(
    data_dir: str | Path,
    charger_id: str,
    *,
    at: str | None = None,
    since: str | None = None,
    until: str | None = None,
    minutes: int = 10,
) -> str:
    if at:
        if since or until:
            raise ValueError("use either --at or --since/--until, not both")
        center = datetime.fromisoformat(_time(at).replace("Z", "+00:00"))
        since = (center - timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")
        until = (center + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")
        heading = f"{charger_id} around {at}"
    else:
        if not since or not until:
            raise ValueError("explain requires --at TIME or --since TIME --until TIME")
        heading = f"{charger_id} from {since} until {until}"

    rows = events_between(data_dir, charger_id=charger_id, since=since, until=until, limit=1000)
    return format_events(rows, heading)
