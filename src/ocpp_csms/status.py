from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ocpp_csms.evidence.store import DATABASE_FILENAME
from ocpp_csms.runtime import process_is_running
from ocpp_csms.rfid.authorization import load_rfid_authorization
from ocpp_csms.transactions.query import TransactionQuery


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


def derive_status(status: str | None, error_code: str | None, info: str | None) -> str | None:
    return 'EmergencyStop' if (status == 'Faulted' and error_code == 'InternalError' and info == 'EmergencyStop') else status


def _connect(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    return connection


def appliance_status(data_dir: str | Path) -> dict[str, Any]:
    root = Path(data_dir).expanduser()
    database = root / DATABASE_FILENAME
    transactions = root / "transactions"
    running = process_is_running(root)
    rfid_authorization = load_rfid_authorization(root)
    active_chargers = sorted(
        {
            view.charge_point_id
            for view in TransactionQuery(root).active()
            if view.charge_point_id
        }
    )
    result: dict[str, Any] = {
        "data_dir": str(root),
        "database": "missing",
        "transactions": "missing",
        "server": "running" if running else "stopped",
        "started_at": None,
        "chargers": [],
        "active_chargers": active_chargers,
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
            with _connect(database) as connection:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version < 2:
                    result["database"] = "upgrade-needed"
                else:
                    result["database"] = "ok"
                    if running:
                        row = connection.execute(
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
        result["chargers"] = charger_statuses(database, live_since_id=live_since_id)
    return result


def charger_statuses(
    database: Path,
    charger_id: str | None = None,
    *,
    live_since_id: int | None = None,
) -> list[ChargerStatus]:
    if not database.exists():
        return []
    with _connect(database) as connection:
        if charger_id is None:
            rows = connection.execute(
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
            _charger_status(connection, value, live_since_id=live_since_id)
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
                derived_status=derive_status(status['status'], status['error_code'], status['info']) if status else None,
                transaction_id=int(transaction["transaction_id"]) if transaction else None,
                id_tag=transaction["id_tag"] if transaction else None,
                started_at=(transaction["started_at"] or transaction["start_received_at"]) if transaction else None,
            )
        )

    transaction = transaction_rows[0] if transaction_rows else None
    summary_status = None
    summary_error = None
    summary_info = None
    if transaction and transaction["connector_id"] is not None:
        active_connector = statuses.get(int(transaction["connector_id"]))
        if active_connector:
            summary_status = active_connector["status"]
            summary_error = active_connector["error_code"]
            summary_info = active_connector['info']
        else:
            summary_status = "Charging"
    elif status_rows:
        latest_status = max(status_rows, key=lambda row: row["received_at"])
        summary_status = latest_status["status"]
        summary_error = latest_status["error_code"]
        summary_info = latest_status['info']

    # An emergency fault on any physical connector must be visible at charger level,
    # even when connector 0 reports Available or no transaction ever existed.
    emergencies = [row for row in status_rows if int(row['connector_id']) != 0 and derive_status(row['status'], row['error_code'], row['info']) == 'EmergencyStop']
    if emergencies:
        fault = max(emergencies, key=lambda row: row['received_at'])
        summary_status, summary_error, summary_info = fault['status'], fault['error_code'], fault['info']

    return ChargerStatus(
        charger_id=charger_id,
        connected=connected,
        connected_at=runtime["occurred_at"] if connected else None,
        subprotocol=subprotocol,
        last_seen=latest["received_at"] if latest else None,
        status=summary_status,
        error_code=summary_error,
        info=summary_info,
        derived_status=derive_status(summary_status, summary_error, summary_info),
        transaction_id=int(transaction["transaction_id"]) if transaction else None,
        id_tag=transaction["id_tag"] if transaction else None,
        started_at=(transaction["started_at"] or transaction["start_received_at"]) if transaction else None,
        connectors=connectors,
    )


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
            f"Connected since: {item.connected_at or '-'}",
            f"Protocol: {item.subprotocol or 'not negotiated'}",
            f"Last seen: {item.last_seen or '-'}",
            f"Status: {item.derived_status or item.status or 'Unknown'}",
            *( [f"OCPP status: {item.status}"] if item.derived_status != item.status else [] ),
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
                        f"  Charging: {'yes' if connector.transaction_id is not None else 'no'}",
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
        f"Started: {data.get('started_at') or '-'}",
        f"Database: {data.get('database', 'unknown')}",
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
    lines.append("ID                 Connected  Status       Charging  Last seen")
    for item in chargers:
        lines.append(
            f"{item.charger_id:<18} {'yes' if item.connected else 'no':<10} {(item.derived_status or item.status or 'Unknown'):<12} {'yes' if item.charger_id in active_chargers else 'no':<9} {item.last_seen or '-'}"
        )
    return "\n".join(lines)
