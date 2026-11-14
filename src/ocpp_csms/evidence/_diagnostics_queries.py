"""Private read-only SQLite diagnostics queries.

Connection ownership, validation and dynamic filters remain in diagnostics.py.
"""
from __future__ import annotations

import sqlite3
from typing import Any


def select_events_between(
    connection: sqlite3.Connection,
    *,
    tx_condition: str,
    action_where: str,
    params: list[Any],
    limit: int | None,
) -> list[sqlite3.Row]:
    return connection.execute(
        f"""
            SELECT * FROM (
                SELECT id, received_at AS occurred_at, charger_id, 'ocpp' AS kind,
                       action, direction, transaction_id, id_tag, payload_json AS payload
                FROM events
                UNION ALL
                SELECT id, occurred_at, charger_id, 'runtime', event, NULL, NULL, NULL, details_json
                FROM runtime_events
            )
            WHERE (? IS NULL OR charger_id = ?)
              {tx_condition}
              AND (? IS NULL OR occurred_at >= ?)
              AND (? IS NULL OR occurred_at <= ?)
              {action_where}
            ORDER BY occurred_at DESC, id DESC
            LIMIT ?
            """,
        (*params, limit if limit is not None else -1),
    ).fetchall()

def select_transaction_events(
    connection: sqlite3.Connection,
    transaction_id: int,
    limit: int,
) -> list[sqlite3.Row]:
    return connection.execute(
        """
            SELECT id, received_at AS occurred_at, charger_id, 'ocpp' AS kind,
                   action, direction, transaction_id, id_tag, payload_json AS payload
            FROM events
            WHERE transaction_id = ?
            ORDER BY occurred_at ASC, id ASC
            LIMIT ?
            """,
        (transaction_id, limit),
    ).fetchall()
