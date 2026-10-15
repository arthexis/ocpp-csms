"""Read-only committed OCPP evidence inspection for stopped cutover candidates.

Call only after the candidate service has exited. Any inspection failure must
prohibit automatic rollback; this module never initializes a database.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from ocpp_csms.schema import DATABASE_FILENAME


def has_committed_ocpp_evidence(data_dir: str | Path) -> bool:
    path = Path(data_dir).expanduser() / DATABASE_FILENAME
    if not path.is_file():
        raise RuntimeError(f"candidate database missing: {path}")
    try:
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=0.2)) as db:
            db.execute("PRAGMA query_only = ON")
            db.execute("PRAGMA busy_timeout = 200")
            # Real OCPP frames are committed to events; these derived tables
            # guard against a partially persisted frame or an unexpected writer.
            for table in ("events", "transactions", "connector_status", "start_identities"):
                if db.execute(f"SELECT EXISTS(SELECT 1 FROM {table})").fetchone()[0]:
                    return True
            return False
    except sqlite3.Error as exc:
        raise RuntimeError(f"candidate evidence inspection uncertain: {exc}") from exc

