"""Private status SQLite reads; caller owns connections and state decisions."""
from __future__ import annotations

import sqlite3


def latest_runtime_event(connection: sqlite3.Connection, charger_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT id, occurred_at, event FROM runtime_events
        WHERE charger_id = ? AND event IN ('charger_connected', 'charger_disconnected')
        ORDER BY id DESC LIMIT 1
        """,
        (charger_id,),
    ).fetchone()


def latest_connection_metadata(connection: sqlite3.Connection, charger_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT details_json FROM runtime_events
        WHERE charger_id = ? AND event = 'charger_connected'
        ORDER BY id DESC LIMIT 1
        """,
        (charger_id,),
    ).fetchone()


def latest_inbound_event(connection: sqlite3.Connection, charger_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT received_at FROM events
        WHERE charger_id = ? AND direction = 'in'
        ORDER BY id DESC LIMIT 1
        """,
        (charger_id,),
    ).fetchone()


def connector_status_rows(connection: sqlite3.Connection, charger_id: str) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT connector_id, status, error_code, info, received_at
        FROM connector_status
        WHERE charger_id = ?
        ORDER BY connector_id
        """,
        (charger_id,),
    ).fetchall()


def open_transaction_rows(connection: sqlite3.Connection, charger_id: str) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT transaction_id, connector_id, id_tag, started_at, start_received_at
        FROM transactions
        WHERE charger_id = ? AND state = 'open'
        ORDER BY start_received_at DESC
        """,
        (charger_id,),
    ).fetchall()


