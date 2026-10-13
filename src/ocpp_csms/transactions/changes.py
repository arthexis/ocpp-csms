from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ocpp_csms.schema import source_id

_DB = "transaction-changes.sqlite3"


def fingerprint(record: dict[str, Any]) -> str:
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _connect(data_dir: str | Path) -> sqlite3.Connection:
    path = Path(data_dir) / _DB
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("""CREATE TABLE IF NOT EXISTS changes (
        change_id INTEGER PRIMARY KEY AUTOINCREMENT,
        transaction_id INTEGER NOT NULL,
        fingerprint TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        record_json TEXT NOT NULL
    )""")
    return connection


def record_change(data_dir: str | Path, record: dict[str, Any]) -> None:
    """Journal a committed archive revision; reconciliation repairs the archive/journal crash gap."""
    with _connect(data_dir) as connection:
        connection.execute(
            "INSERT INTO changes (transaction_id, fingerprint, observed_at, record_json) VALUES (?, ?, ?, ?)",
            (int(record["transaction_id"]), fingerprint(record),
             datetime.now(timezone.utc).isoformat(), json.dumps(record, sort_keys=True)),
        )


def reconcile(data_dir: str | Path) -> None:
    """Capture archive-only edits and any write completed before journal commit."""
    root = Path(data_dir) / "transactions"
    with _connect(data_dir) as connection:
        for path in sorted(root.glob("*/*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                transaction_id = int(record["transaction_id"])
                digest = fingerprint(record)
            except (OSError, ValueError, TypeError, KeyError):
                continue
            last = connection.execute(
                "SELECT fingerprint FROM changes WHERE transaction_id = ? ORDER BY change_id DESC LIMIT 1",
                (transaction_id,),
            ).fetchone()
            if last is None or last[0] != digest:
                connection.execute(
                    "INSERT INTO changes (transaction_id, fingerprint, observed_at, record_json) VALUES (?, ?, ?, ?)",
                    (transaction_id, digest, datetime.now(timezone.utc).isoformat(),
                     json.dumps(record, sort_keys=True)),
                )


def changes_page(data_dir: str | Path, *, after: int = 0, limit: int = 500) -> dict[str, Any]:
    if after < 0 or limit < 1:
        raise ValueError("invalid transaction change cursor")
    reconcile(data_dir)
    with _connect(data_dir) as connection:
        rows = connection.execute(
            "SELECT change_id, transaction_id, fingerprint, observed_at, record_json "
            "FROM changes WHERE change_id > ? ORDER BY change_id LIMIT ?",
            (after, limit + 1),
        ).fetchall()
    selected = rows[:limit]
    return {
        "cursor": {"after": after, "next": selected[-1][0] if selected else after,
                   "more": len(rows) > limit},
        "items": [
            {"change_id": row[0], "transaction_id": row[1], "fingerprint": row[2],
             "observed_at": row[3], "record": json.loads(row[4])}
            for row in selected
        ],
    }
