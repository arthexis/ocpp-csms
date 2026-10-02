from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ocpp_csms.events import DATABASE_FILENAME


@dataclass
class ChargerStatus:
    charger_id: str
    connected: bool
    connected_at: str | None
    last_seen: str | None
    status: str | None
    error_code: str | None
    transaction_id: int | None
    id_tag: str | None
    started_at: str | None


def _connect(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    return connection


def appliance_status(data_dir: str | Path) -> dict[str, Any]:
    root = Path(data_dir).expanduser()
    database = root / DATABASE_FILENAME
    transactions = root / "transactions"
    result: dict[str, Any] = {
        "data_dir": str(root),
        "database": "missing",
        "transactions": "missing",
        "server": "unknown",
        "started_at": None,
        "chargers": [],
    }
    if database.exists():
        try:
            with _connect(database) as connection:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version < 2:
                    result["database"] = "upgrade-needed"
                else:
                    result["database"] = "ok"
                    row = connection.execute(
                        """
                        SELECT occurred_at, event FROM runtime_events
                        WHERE event IN ('server_started', 'server_stopped')
                        ORDER BY id DESC LIMIT 1
                        """
                    ).fetchone()
                    if row:
                        result["server"] = "running" if row["event"] == "server_started" else "stopped"
                        if row["event"] == "server_started":
                            result["started_at"] = row["occurred_at"]
        except sqlite3.Error:
            result["database"] = "error"
    if transactions.exists():
        result["transactions"] = "ok" if transactions.is_dir() else "error"
    if result["database"] == "ok":
        result["chargers"] = charger_statuses(database)
    return result


def charger_statuses(database: Path, charger_id: str | None = None) -> list[ChargerStatus]:
    if not database.exists():
        return []
    with _connect(database) as connection:
        if charger_id is None:
            rows = connection.execute(
                "SELECT DISTINCT charger_id FROM events UNION SELECT DISTINCT charger_id FROM runtime_events WHERE charger_id IS NOT NULL"
            ).fetchall()
            charger_ids = sorted(row[0] for row in rows if row[0])
        else:
            charger_ids = [charger_id]
        return [_charger_status(connection, value) for value in charger_ids]


def _charger_status(connection: sqlite3.Connection, charger_id: str) -> ChargerStatus:
    runtime = connection.execute(
        """
        SELECT occurred_at, event FROM runtime_events
        WHERE charger_id = ? AND event IN ('charger_connected', 'charger_disconnected')
        ORDER BY id DESC LIMIT 1
        """,
        (charger_id,),
    ).fetchone()
    connected = bool(runtime and runtime["event"] == "charger_connected")

    latest = connection.execute(
        """
        SELECT received_at FROM events
        WHERE charger_id = ? AND direction = 'in'
        ORDER BY id DESC LIMIT 1
        """,
        (charger_id,),
    ).fetchone()
    status = connection.execute(
        """
        SELECT status, error_code FROM connector_status
        WHERE charger_id = ? ORDER BY received_at DESC LIMIT 1
        """,
        (charger_id,),
    ).fetchone()
    transaction = connection.execute(
        """
        SELECT transaction_id, id_tag, started_at, start_received_at
        FROM transactions
        WHERE charger_id = ? AND state = 'open'
        ORDER BY start_received_at DESC LIMIT 1
        """,
        (charger_id,),
    ).fetchone()

    return ChargerStatus(
        charger_id=charger_id,
        connected=connected,
        connected_at=runtime["occurred_at"] if connected else None,
        last_seen=latest["received_at"] if latest else None,
        status=status["status"] if status else None,
        error_code=status["error_code"] if status else None,
        transaction_id=int(transaction["transaction_id"]) if transaction else None,
        id_tag=transaction["id_tag"] if transaction else None,
        started_at=(transaction["started_at"] or transaction["start_received_at"]) if transaction else None,
    )


def format_status(data: dict[str, Any], *, charger_id: str | None = None, charging_only: bool = False) -> str:
    chargers: list[ChargerStatus] = data.get("chargers", [])
    if charger_id is not None:
        chargers = [item for item in chargers if item.charger_id == charger_id]
    if charging_only:
        chargers = [item for item in chargers if item.transaction_id is not None]

    if charger_id is not None:
        if not chargers:
            return f"{charger_id}: no recorded activity"
        item = chargers[0]
        lines = [
            item.charger_id,
            f"Connected: {'yes' if item.connected else 'no'}",
            f"Connected since: {item.connected_at or '-'}",
            f"Last seen: {item.last_seen or '-'}",
            f"Status: {item.status or 'Unknown'}",
        ]
        if item.error_code and item.error_code != "NoError":
            lines.append(f"Error: {item.error_code}")
        lines.extend(
            [
                f"Charging: {'yes' if item.transaction_id is not None else 'no'}",
                f"Transaction: {item.transaction_id if item.transaction_id is not None else '-'}",
                f"RFID: {item.id_tag or '-'}",
                f"Started: {item.started_at or '-'}",
            ]
        )
        return "\n".join(lines)

    lines = [
        f"CSMS: {data.get('server', 'unknown')}",
        f"Started: {data.get('started_at') or '-'}",
        f"Database: {data.get('database', 'unknown')}",
        f"JSON archive: {data.get('transactions', 'unknown')}",
        f"Data dir: {data.get('data_dir', '-')}",
        "",
    ]
    if not chargers:
        lines.append("No chargers recorded.")
        return "\n".join(lines)

    lines.append("Chargers:")
    lines.append("ID                 Connected  Status       Charging  Last seen")
    for item in chargers:
        lines.append(
            f"{item.charger_id:<18} {'yes' if item.connected else 'no':<10} {(item.status or 'Unknown'):<12} {'yes' if item.transaction_id is not None else 'no':<9} {item.last_seen or '-'}"
        )
    return "\n".join(lines)
