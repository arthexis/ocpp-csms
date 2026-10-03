from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


DATABASE_FILENAME = "ocpp-csms.sqlite3"


def baseline_observation(data_dir: str, charger: str) -> dict[str, Any]:
    database = Path(data_dir).expanduser() / DATABASE_FILENAME
    result: dict[str, Any] = {
        "database": str(database),
        "connected": False,
        "heartbeat_count": 0,
        "last_heartbeat": None,
        "active_transactions": [],
    }
    if not database.exists():
        return result

    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        with connection:
            runtime = connection.execute(
                """
                SELECT event FROM runtime_events
                WHERE charger_id = ? AND event IN ('charger_connected', 'charger_disconnected')
                ORDER BY id DESC LIMIT 1
                """,
                (charger,),
            ).fetchone()
            heartbeat = connection.execute(
                """
                SELECT COUNT(*) AS count, MAX(received_at) AS latest
                FROM events
                WHERE charger_id = ? AND action = 'Heartbeat' AND direction = 'in'
                """,
                (charger,),
            ).fetchone()
            transactions = connection.execute(
                """
                SELECT transaction_id FROM transactions
                WHERE charger_id = ? AND state = 'open'
                ORDER BY transaction_id
                """,
                (charger,),
            ).fetchall()
    except sqlite3.Error:
        return result

    result["connected"] = bool(runtime and runtime["event"] == "charger_connected")
    if heartbeat:
        result["heartbeat_count"] = int(heartbeat["count"] or 0)
        result["last_heartbeat"] = heartbeat["latest"]
    result["active_transactions"] = [int(row["transaction_id"]) for row in transactions]
    return result
