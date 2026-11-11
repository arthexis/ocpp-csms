from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from ocpp_csms.evidence.store import DATABASE_FILENAME


def physical_connector_ids(data_dir: str | Path, charger_id: str) -> list[int]:
    """Return known physical connector IDs for one charger.

    Connector 0 is the OCPP charge-point scope, not a physical connector, so it
    is deliberately excluded from compatibility fan-out candidates.
    """
    database = Path(data_dir).expanduser() / DATABASE_FILENAME
    # A diagnostic query must not create a missing database or leave an open
    # handle. as_uri() escapes path characters that would otherwise change
    # SQLite's URI query parameters.
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        rows = connection.execute(
            """
            SELECT connector_id
            FROM connector_status
            WHERE charger_id = ? AND connector_id > 0
            ORDER BY connector_id
            """,
            (charger_id,),
        ).fetchall()
    return [int(row[0]) for row in rows]
