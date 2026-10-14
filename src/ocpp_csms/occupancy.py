"""Read-only connector occupancy from current-connection evidence."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from ocpp_csms.evidence.store import DATABASE_FILENAME

BUSY_STATUSES = frozenset({"Charging", "SuspendedEV", "SuspendedEVSE", "Preparing", "Finishing", "Reserved"})


def connector_occupancy(data_dir: str | Path, charger: str) -> dict[int, str]:
    with sqlite3.connect(f"file:{Path(data_dir) / DATABASE_FILENAME}?mode=ro", uri=True) as connection:
        connected = connection.execute(
            """SELECT occurred_at FROM runtime_events
               WHERE charger_id = ? AND event = 'charger_connected'
               ORDER BY id DESC LIMIT 1""", (charger,)
        ).fetchone()
        if connected is None:
            return {}
        rows = connection.execute(
            """SELECT connector_id, status, received_at FROM connector_status
               WHERE charger_id = ? AND connector_id > 0""", (charger,)
        ).fetchall()
    return {int(cid): status for cid, status, received in rows if received >= connected[0]}
