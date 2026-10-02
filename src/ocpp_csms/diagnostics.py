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
                SELECT received_at AS occurred_at, charger_id, 'ocpp' AS kind,
                       action, direction, transaction_id, id_tag, payload_json AS payload
                FROM events
                UNION ALL
                SELECT occurred_at, charger_id, 'runtime', event, NULL, NULL, NULL, details_json
                FROM runtime_events
            )
            WHERE (? IS NULL OR charger_id = ?)
              AND (? IS NULL OR occurred_at >= ?)
              AND (? IS NULL OR occurred_at <= ?)
            ORDER BY occurred_at DESC
            LIMIT ?
            """,
            (charger_id, charger_id, since, since, until, until, limit),
        ).fetchall()
    finally:
        connection.close()
    rows.reverse()
    return rows


def _summary(row: sqlite3.Row) -> str:
    if row["kind"] == "runtime":
        return row["action"].replace("_", " ")

    payload: dict[str, Any] = json.loads(row["payload"] or "{}")
    if row["direction"] == "out":
        status = payload.get("status")
        info = payload.get("idTagInfo")
        if status is None and isinstance(info, dict):
            status = info.get("status")
        suffix = f" {status}" if status else " response"
        if row["transaction_id"] is not None:
            suffix += f" tx={row['transaction_id']}"
        return f"→ {row['action']}{suffix}"

    details = []
    if row["id_tag"]:
        details.append(f"RFID {row['id_tag']}")
    if row["transaction_id"] is not None:
        details.append(f"tx={row['transaction_id']}")
    if row["action"] == "StatusNotification":
        details.extend(str(value) for value in (payload.get("status"), payload.get("error_code")) if value and value != "NoError")
    return " ".join([row["action"], *details])


def format_events(rows: list[sqlite3.Row], heading: str | None = None) -> str:
    if not rows:
        return "No matching events."
    lines = [heading, ""] if heading else []
    for row in rows:
        charger = f" {row['charger_id']}" if row["charger_id"] else ""
        lines.append(f"{row['occurred_at']}{charger}  {_summary(row)}")
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
