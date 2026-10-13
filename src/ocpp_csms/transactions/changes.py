from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from ocpp_csms.schema import database_path
from ocpp_csms.time import utc_now_iso


def canonical_record(record: dict[str, Any]) -> str:
    """Stable representation of one complete archived revision."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def reconcile_transaction_changes(data_dir: str | Path) -> int:
    """Append immutable revisions for archive states not yet journaled.

    This is safe to rerun after interruption between JSON replacement and
    SQLite commit. It also discovers archive-only external corrections.
    The archive is authoritative; never infer a revision for unresolved evidence.
    """
    root = Path(data_dir).expanduser()
    db = database_path(root)
    if not db.exists():
        return 0
    inserted = 0
    with sqlite3.connect(db, timeout=5) as connection:
        connection.execute("BEGIN IMMEDIATE")
        latest = {
            int(transaction_id): fingerprint
            for transaction_id, fingerprint in connection.execute(
                """SELECT c.transaction_id, c.content_sha256
                   FROM transaction_changes c
                   WHERE c.change_id = (
                       SELECT MAX(x.change_id) FROM transaction_changes x
                       WHERE x.transaction_id = c.transaction_id
                   )"""
            )
        }
        for path in sorted((root / "transactions").glob("*/*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(record, dict):
                    continue
                transaction_id = int(record["transaction_id"])
                charger_id = str(record["charge_point_id"])
                serialized = canonical_record(record)
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                continue
            fingerprint = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
            if latest.get(transaction_id) == fingerprint:
                continue
            connection.execute(
                """INSERT INTO transaction_changes
                   (transaction_id, charger_id, changed_at, content_sha256, record_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (transaction_id, charger_id, utc_now_iso(), fingerprint, serialized),
            )
            latest[transaction_id] = fingerprint
            inserted += 1
    return inserted
