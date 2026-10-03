from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

from ocpp_csms.events import DATABASE_FILENAME


async def _send_control(path: str, request: dict[str, Any]) -> dict[str, Any]:
    reader, writer = await asyncio.open_unix_connection(str(Path(path).expanduser()))
    try:
        writer.write(json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n")
        await writer.drain()
        line = await reader.readline()
        if not line:
            raise ConnectionError("control socket closed without response")
        response = json.loads(line)
        if not isinstance(response, dict):
            raise ValueError("invalid control response")
        return response
    finally:
        writer.close()
        await writer.wait_closed()


def send_control(path: str, request: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(_send_control(path, request))


def latest_event_id(data_dir: str) -> int:
    database = Path(data_dir).expanduser() / DATABASE_FILENAME
    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()
    return int(row[0])


def reboot_observation(data_dir: str, charger: str, *, after_event_id: int) -> dict[str, Any]:
    database = Path(data_dir).expanduser() / DATABASE_FILENAME
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """
            SELECT id, action, direction, received_at, payload_json
            FROM events
            WHERE charger_id = ? AND id > ?
            ORDER BY id
            """,
            (charger, after_event_id),
        ).fetchall()
        runtime = connection.execute(
            """
            SELECT event FROM runtime_events
            WHERE charger_id = ? AND event IN ('charger_connected', 'charger_disconnected')
            ORDER BY id
            """,
            (charger,),
        ).fetchall()
    inbound = [row for row in rows if row[2] == "in"]
    actions = [row[1] for row in inbound]
    runtime_events = [row[0] for row in runtime]
    return {
        "boot_notification": "BootNotification" in actions,
        "heartbeat": "Heartbeat" in actions,
        "disconnect_seen": "charger_disconnected" in runtime_events,
        "reconnect_seen": "charger_connected" in runtime_events,
        "actions": actions,
    }


def configuration_payload(response: dict[str, Any]) -> dict[str, Any]:
    if response.get("ok") is not True:
        raise RuntimeError(str(response.get("error") or "configuration request failed"))
    payload = response.get("response")
    if not isinstance(payload, dict):
        raise ValueError("invalid configuration response")
    rows = payload.get("configuration_key", []) or []
    unknown = payload.get("unknown_key", []) or []
    if not isinstance(rows, list) or not isinstance(unknown, list):
        raise ValueError("invalid configuration response")
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid configuration response")
        key = row.get("key")
        readonly = row.get("readonly")
        value = row.get("value")
        if not isinstance(key, str) or not isinstance(readonly, bool):
            raise ValueError("invalid configuration response")
        if value is not None and not isinstance(value, str):
            raise ValueError("invalid configuration response")
        normalized.append({"key": key, "readonly": readonly, "value": value})
    if any(not isinstance(key, str) for key in unknown):
        raise ValueError("invalid configuration response")
    return {"configuration_key": normalized, "unknown_key": list(unknown)}


def configuration_map(payload: dict[str, Any]) -> dict[str, tuple[bool, str | None]]:
    return {
        row["key"]: (row["readonly"], row.get("value"))
        for row in payload.get("configuration_key", [])
    }
