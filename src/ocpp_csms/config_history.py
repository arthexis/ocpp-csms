"""Read-only, bounded GetConfiguration history from authoritative CSMS evidence.

Never sends an OCPP request.  Only sanitized values leave the CSMS database.
The historical cursor is an event ID and is entirely independent of export/v1.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable

from ocpp_csms.config_report import configuration_snapshot
from ocpp_csms.output import json_command_result
from ocpp_csms.schema import DATABASE_FILENAME, source_id

HISTORY_SCHEMA = "ocpp-csms/config-snapshots/v1"


def _coverage(db: sqlite3.Connection, charger: str, event_id: int) -> str:
    """Conservative request scope from the immediately preceding config frame."""
    row = db.execute(
        "SELECT direction,payload_json FROM events "
        "WHERE charger_id=? AND action='GetConfiguration' AND id<? "
        "ORDER BY id DESC LIMIT 1", (charger, event_id),
    ).fetchone()
    if row is None or row["direction"] != "out":
        return "unknown"
    try:
        request = json.loads(row["payload_json"])
    except (ValueError, TypeError):
        return "unknown"
    if not isinstance(request, dict):
        return "unknown"
    keys = request.get("key")
    if keys is None or keys == []:
        return "full"
    if isinstance(keys, list) and all(isinstance(key, str) for key in keys) and keys:
        return "partial"
    return "unknown"


def snapshots_for_events(db: sqlite3.Connection, rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    """Derive sanitized evidence without modifying input rows or DB cursors."""
    snapshots: list[dict[str, Any]] = []
    for row in rows:
        if row["action"] != "GetConfiguration" or row["direction"] != "in":
            continue
        raw = row["payload"] if "payload" in row.keys() else row["payload_json"]
        try:
            payload = json.loads(raw or "{}")
        except (ValueError, TypeError):
            continue
        # OCPP 1.6 optional arrays may be explicitly null. The existing
        # config_report normalizer already handles this, but reject malformed
        # non-null values and responses with no useful configuration evidence.
        if not isinstance(payload, dict):
            continue
        keys, unknown = payload.get("configuration_key"), payload.get("unknown_key")
        if ((keys is not None and not isinstance(keys, list))
                or (unknown is not None and not isinstance(unknown, list))
                or not (keys or unknown)):
            continue
        charger = str(row["charger_id"])
        try:
            sanitized = configuration_snapshot(payload, charger=charger)
        except ValueError:
            continue
        event_id = int(row["id"])
        timestamp = row["occurred_at"] if "occurred_at" in row.keys() else row["received_at"]
        snapshots.append({
            "source_event_id": event_id,
            "charger_id": charger,
            "at": timestamp,
            "coverage": _coverage(db, charger, event_id),
            "configuration": sanitized["configuration"],
            "unknown": sanitized["unknown"],
        })
    return snapshots


def export_configuration_history(
    data_dir: str | Path, *, after: int = 0, limit: int = 100,
) -> dict[str, Any]:
    """Paginate past GetConfiguration responses using only read-only SQLite."""
    if after < 0 or not 1 <= limit <= 500:
        raise ValueError("Invalid historical configuration cursor or limit")
    db_path = Path(data_dir).expanduser() / DATABASE_FILENAME
    snapshots: list[dict[str, Any]] = []
    more = False
    if db_path.exists():
        with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN")
            rows = db.execute(
                "SELECT id,received_at,charger_id,action,direction,payload_json "
                "FROM events WHERE action='GetConfiguration' AND direction='in' "
                "AND id>? ORDER BY id LIMIT ?", (after, limit + 1),
            ).fetchall()
            more = len(rows) > limit
            snapshots = snapshots_for_events(db, rows[:limit])
            last_id = int(rows[limit - 1]["id"]) if len(rows) >= limit else (
                int(rows[-1]["id"]) if rows else after)
    else:
        last_id = after
    return json_command_result({
        "source_id": source_id(data_dir),
        "cursor": {"after": after, "next": last_id, "more": more},
        "configuration_snapshots": snapshots,
    }, schema=HISTORY_SCHEMA)
