from __future__ import annotations

import json
import re
import sqlite3
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ocpp_csms.evidence.store import DATABASE_FILENAME
from ocpp_csms.schema import CURRENT_SCHEMA_VERSION
from ocpp_csms.runtime import process_is_running
from ocpp_csms.rfid.authorization import load_rfid_authorization


@dataclass
class ConnectorStatus:
    connector_id: int
    status: str | None
    error_code: str | None
    transaction_id: int | None
    id_tag: str | None
    started_at: str | None
    info: str | None = None
    derived_status: str | None = None


@dataclass
class ChargerStatus:
    charger_id: str
    connected: bool
    connected_at: str | None
    subprotocol: str | None
    last_seen: str | None
    status: str | None
    error_code: str | None
    transaction_id: int | None
    id_tag: str | None
    started_at: str | None
    connectors: list[ConnectorStatus]
    info: str | None = None
    derived_status: str | None = None
    availability: str = "unknown"
    activity: str = "unknown"


def derive_status(status: str | None, error_code: str | None, info: str | None) -> str | None:
    return 'EmergencyStop' if (status == 'Faulted' and error_code == 'InternalError' and info == 'EmergencyStop') else status


def operational_state(connectors: list[ConnectorStatus]) -> tuple[str, str]:
    """Customer availability and observed activity are independent."""
    physical = [item for item in connectors if item.connector_id > 0]
    availability = (
        "available" if any(item.status == "Available" for item in physical)
        else "unavailable" if physical and all(item.status in {
            "Charging", "SuspendedEV", "SuspendedEVSE", "Preparing",
            "Finishing", "Faulted", "Unavailable", "Reserved",
        } for item in physical)
        else "unknown"
    )
    activity = (
        "active" if any(item.status in {"Charging", "SuspendedEV", "SuspendedEVSE"} for item in physical)
        else "inactive" if physical and all(item.status in {"Available", "Unavailable", "Faulted"} for item in physical)
        else "unknown"
    )
    return availability, activity


def _connect(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    return connection


def appliance_status(data_dir: str | Path, *, connection: sqlite3.Connection | None = None) -> dict[str, Any]:
    root = Path(data_dir).expanduser()
    database = root / DATABASE_FILENAME
    transactions = root / "transactions"
    running = process_is_running(root)
    rfid_authorization = load_rfid_authorization(root)
    result: dict[str, Any] = {
        "data_dir": str(root),
        "database": "missing",
        "schema_compiled": CURRENT_SCHEMA_VERSION,
        "schema_database": None,
        "transactions": "missing",
        "server": "running" if running else "stopped",
        "started_at": None,
        "chargers": [],
        "active_chargers": [],
        "rfid_authorization": (
            None
            if rfid_authorization.allow_all
            else {
                "source": rfid_authorization.source.name if rfid_authorization.source else None,
                "entries": len(rfid_authorization.entries),
                "valid": rfid_authorization.valid,
                "error": rfid_authorization.error,
            }
        ),
    }
    live_since_id: int | None = None
    if database.exists():
        try:
            with (nullcontext(connection) if connection is not None else _connect(database)) as db:
                version = int(db.execute("PRAGMA user_version").fetchone()[0])
                result["schema_database"] = version
                if version < 2:
                    result["database"] = "upgrade-needed"
                else:
                    result["database"] = "ok"
                    if running:
                        row = db.execute(
                            """
                            SELECT id, occurred_at FROM runtime_events
                            WHERE event = 'server_started'
                            ORDER BY id DESC LIMIT 1
                            """
                        ).fetchone()
                        if row:
                            live_since_id = int(row["id"])
                            result["started_at"] = row["occurred_at"]
        except sqlite3.Error:
            result["database"] = "error"
    if transactions.exists():
        result["transactions"] = "ok" if transactions.is_dir() else "error"
    if result["database"] == "ok":
        result["chargers"] = charger_statuses(database, live_since_id=live_since_id, connection=connection)
        result["active_chargers"] = sorted({
            charger.charger_id for charger in result["chargers"]
            if any(
                connector.status in {"Charging", "SuspendedEV", "SuspendedEVSE"}
                for connector in charger.connectors
            )
        })
    return result


def charger_statuses(
    database: Path,
    charger_id: str | None = None,
    *,
    live_since_id: int | None = None,
    connection: sqlite3.Connection | None = None,
) -> list[ChargerStatus]:
    if not database.exists():
        return []
    with (nullcontext(connection) if connection is not None else _connect(database)) as db:
        if charger_id is None:
            rows = db.execute(
                """
                SELECT charger_id FROM events
                UNION SELECT charger_id FROM runtime_events WHERE charger_id IS NOT NULL
                UNION SELECT charger_id FROM connector_status
                UNION SELECT charger_id FROM transactions
                """
            ).fetchall()
            charger_ids = sorted(row[0] for row in rows if row[0])
        else:
            charger_ids = [charger_id]
        return [
            _charger_status(db, value, live_since_id=live_since_id)
            for value in charger_ids
        ]


def _charger_status(
    connection: sqlite3.Connection,
    charger_id: str,
    *,
    live_since_id: int | None = None,
) -> ChargerStatus:
    runtime = connection.execute(
        """
        SELECT id, occurred_at, event FROM runtime_events
        WHERE charger_id = ? AND event IN ('charger_connected', 'charger_disconnected')
        ORDER BY id DESC LIMIT 1
        """,
        (charger_id,),
    ).fetchone()
    connected = bool(
        runtime
        and runtime["event"] == "charger_connected"
        and (live_since_id is None or int(runtime["id"]) > live_since_id)
    )

    connection_event = connection.execute(
        """
        SELECT details_json FROM runtime_events
        WHERE charger_id = ? AND event = 'charger_connected'
        ORDER BY id DESC LIMIT 1
        """,
        (charger_id,),
    ).fetchone()
    subprotocol = None
    if connection_event and connection_event["details_json"]:
        try:
            details = json.loads(connection_event["details_json"])
            subprotocol = details.get("subprotocol")
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

    latest = connection.execute(
        """
        SELECT received_at FROM events
        WHERE charger_id = ? AND direction = 'in'
        ORDER BY id DESC LIMIT 1
        """,
        (charger_id,),
    ).fetchone()
    status_rows = connection.execute(
        """
        SELECT connector_id, status, error_code, info, received_at
        FROM connector_status
        WHERE charger_id = ?
        ORDER BY connector_id
        """,
        (charger_id,),
    ).fetchall()
    transaction_rows = connection.execute(
        """
        SELECT transaction_id, connector_id, id_tag, started_at, start_received_at
        FROM transactions
        WHERE charger_id = ? AND state = 'open'
        ORDER BY start_received_at DESC
        """,
        (charger_id,),
    ).fetchall()

    statuses = {int(row["connector_id"]): row for row in status_rows}
    transactions: dict[int, sqlite3.Row] = {}
    for row in transaction_rows:
        if row["connector_id"] is not None:
            transactions.setdefault(int(row["connector_id"]), row)

    connector_ids = sorted(set(statuses) | set(transactions))
    connectors = []
    for connector_id in connector_ids:
        status = statuses.get(connector_id)
        transaction = transactions.get(connector_id)
        connectors.append(
            ConnectorStatus(
                connector_id=connector_id,
                status=status["status"] if status else None,
                error_code=status["error_code"] if status else None,
                info=status['info'] if status else None,
                derived_status=(derive_status(status['status'], status['error_code'], status['info']) if status
                                else "Unknown" if transaction is not None
                                else "Available" if connected and connector_id > 0
                                else None),
                transaction_id=int(transaction["transaction_id"]) if transaction else None,
                id_tag=transaction["id_tag"] if transaction else None,
                started_at=(transaction["started_at"] or transaction["start_received_at"]) if transaction else None,
            )
        )

    # Operational status comes from reported connector evidence, not historical
    # SQLite transactions that may remain open indefinitely after power loss.
    transaction = transaction_rows[0] if transaction_rows else None
    summary_status = None
    summary_error = None
    summary_info = None
    if status_rows:
        # Prefer a currently reported busy physical connector over a generic
        # charge-point/other-connector Available notification.
        busy = [row for row in status_rows if int(row["connector_id"]) > 0 and row["status"] in {"Charging", "SuspendedEV", "SuspendedEVSE"}]
        latest_status = max(busy or status_rows, key=lambda row: row["received_at"])
        summary_status = latest_status["status"]
        summary_error = latest_status["error_code"]
        summary_info = latest_status["info"]

    # An emergency fault on any physical connector must be visible at charger level,
    # even when connector 0 reports Available or no transaction ever existed.
    emergencies = [row for row in status_rows if int(row['connector_id']) != 0 and derive_status(row['status'], row['error_code'], row['info']) == 'EmergencyStop']
    if emergencies:
        fault = max(emergencies, key=lambda row: row['received_at'])
        summary_status, summary_error, summary_info = fault['status'], fault['error_code'], fault['info']

    availability, activity = operational_state(connectors)
    # A connected charger with no contrary evidence is operationally available.
    # Preserve the actual OCPP status separately; do not invent a notification.
    assumed_available = connected and summary_status is None and not transaction_rows
    if assumed_available:
        availability, activity = "available", "inactive"
    return ChargerStatus(
        charger_id=charger_id,
        connected=connected,
        connected_at=runtime["occurred_at"] if connected else None,
        subprotocol=subprotocol,
        last_seen=latest["received_at"] if latest else None,
        status=summary_status,
        error_code=summary_error,
        info=summary_info,
        derived_status=("Available" if assumed_available else derive_status(summary_status, summary_error, summary_info)),
        transaction_id=int(transaction["transaction_id"]) if transaction else None,
        id_tag=transaction["id_tag"] if transaction else None,
        started_at=(transaction["started_at"] or transaction["start_received_at"]) if transaction else None,
        connectors=connectors,
        availability=availability if connected else "unknown",
        activity=activity if connected else "unknown",
    )


def display_timestamp(value: str | None) -> str:
    """Drop fractional seconds for human-readable timestamps only."""
    return re.sub(r"(\d{2}:\d{2}:\d{2})\.\d+", r"\1", value) if value else "-"


def connected_duration(connected: bool, connected_at: str | None, *, now: datetime | None = None) -> str:
    """Human-readable duration of the current connection, not historical uptime."""
    if not connected:
        return "no"
    if not connected_at:
        return "unknown"
    try:
        started = datetime.fromisoformat(connected_at.replace("Z", "+00:00"))
        if started.tzinfo is None:
            return "unknown"
    except ValueError:
        return "unknown"
    current = now or datetime.now(timezone.utc)
    elapsed = max(0, int((current - started).total_seconds()))
    if elapsed < 60:
        return f"{elapsed}s"
    minutes = elapsed // 60
    days, remainder = divmod(minutes, 1440)
    hours, mins = divmod(remainder, 60)
    if days:
        return f"{days}d {hours}h" if hours else f"{days}d {mins}m" if mins else f"{days}d"
    if hours:
        return f"{hours}h {mins}m" if mins else f"{hours}h"
    return f"{mins}m"


def format_status(data: dict[str, Any], *, charger_id: str | None = None, charging_only: bool = False, appliance: bool = True) -> str:
    chargers: list[ChargerStatus] = data.get("chargers", [])
    active_chargers = set(data.get("active_chargers", []))
    if charger_id is not None:
        chargers = [item for item in chargers if item.charger_id == charger_id]
    if charging_only:
        chargers = [item for item in chargers if item.charger_id in active_chargers]

    if charger_id is not None:
        if not chargers:
            return f"{charger_id}: no recorded activity"
        item = chargers[0]
        lines = [
            item.charger_id,
            f"Connected: {'yes' if item.connected else 'no'}",
            f"Connected since: {display_timestamp(item.connected_at)}",
            f"Protocol: {item.subprotocol or 'not negotiated'}",
            f"Last seen: {display_timestamp(item.last_seen)}",
            f"Status: {item.derived_status or item.status or 'Unknown'}",
            *( [f"OCPP status: {item.status or 'not reported'}"] if item.derived_status != item.status else [] ),
        ]
        if item.error_code and item.error_code != "NoError":
            lines.append(f"Error: {item.error_code}")
        if item.info:
            lines.append(f"Info: {item.info}")
        if item.connectors:
            lines.append("Connectors:")
            for connector in item.connectors:
                lines.append(f"Connector {connector.connector_id}: {connector.derived_status or connector.status or 'Unknown'}")
                if connector.error_code and connector.error_code != "NoError":
                    lines.append(f"  Error: {connector.error_code}")
                if connector.info:
                    lines.append(f"  Info: {connector.info}")
                lines.extend(
                    [
                        f"  Charging: {'yes' if connector.status == 'Charging' else 'no' if connector.status in {'Available', 'Finishing', 'Unavailable', 'Faulted'} else 'unknown'}",
                        f"  Transaction: {connector.transaction_id if connector.transaction_id is not None else '-'}",
                        f"  RFID: {connector.id_tag or '-'}",
                        f"  Started: {connector.started_at or '-'}",
                    ]
                )
        else:
            lines.extend(
                [
                    f"Charging: {'yes' if item.charger_id in active_chargers else 'no'}",
                    f"Transaction: {item.transaction_id if item.transaction_id is not None else '-'}",
                    f"RFID: {item.id_tag or '-'}",
                    f"Started: {item.started_at or '-'}",
                ]
            )
        return "\n".join(lines)

    lines = []
    if appliance:
        lines.extend([
        f"CSMS: {data.get('server', 'unknown')}",
        f"Started: {display_timestamp(data.get('started_at'))}",
        f"Database: {data.get('database', 'unknown')}",
        f"Schema (code): {data.get('schema_compiled', CURRENT_SCHEMA_VERSION)}",
        f"Schema (database): {data.get('schema_database') if data.get('schema_database') is not None else '-'}",
        f"JSON archive: {data.get('transactions', 'unknown')}",
        (
            "RFID authorization: Allow All"
            if data.get("rfid_authorization") is None
            else (
                f"RFID authorization: {data['rfid_authorization'].get('source', 'rfid.csv')} (invalid)"
                if not data["rfid_authorization"].get("valid", False)
                else f"RFID authorization: {data['rfid_authorization'].get('source', 'rfid.csv')} ({data['rfid_authorization'].get('entries', 0)} cards)"
            )
        ),
        f"Data dir: {data.get('data_dir', '-')}",
        "",
        ])
    if not chargers:
        lines.append("No chargers recorded.")
        return "\n".join(lines)

    if appliance:
        lines.append("Chargers:")
    physical_ids = sorted({
        connector.connector_id for item in chargers
        for connector in item.connectors if connector.connector_id > 0
    })
    # Never invent C2 merely because a charger model might support it.
    status_headers = [f"State C{connector_id}" for connector_id in physical_ids] if physical_ids else ["Status"]
    widths = [max(12, len(header)) for header in status_headers]
    id_width = max(22, *(len(item.charger_id) + 2 for item in chargers))
    lines.append(f"{'ID':<{id_width}} {'Connected':<10} " +
                 "  ".join(f"{header:<{width}}" for header, width in zip(status_headers, widths)) +
                 "  Last seen")
    now = datetime.now(timezone.utc)
    for item in chargers:
        by_connector = {connector.connector_id: connector for connector in item.connectors}
        statuses = [
            (by_connector[connector_id].derived_status or by_connector[connector_id].status or "Unknown")
            if connector_id in by_connector else "-"
            for connector_id in physical_ids
        ] if physical_ids else [item.derived_status or item.status or "Unknown"]
        lines.append(
            f"{item.charger_id:<{id_width}} {connected_duration(item.connected, item.connected_at, now=now):<10} " +
            "  ".join(f"{value:<{width}}" for value, width in zip(statuses, widths)) +
            f"  {display_timestamp(item.last_seen)}"
        )
    return "\n".join(lines)
