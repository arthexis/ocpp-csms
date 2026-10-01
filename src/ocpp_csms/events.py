from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ocpp_csms.time import utc_now_iso

_SCHEMA_VERSION = 1


class EventStore:
    """Small append-only SQLite store for operational evidence."""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir).expanduser()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "events.sqlite3"
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > _SCHEMA_VERSION:
                raise RuntimeError(
                    f"events database schema {version} is newer than supported {_SCHEMA_VERSION}"
                )
            if version == 0:
                connection.executescript(
                    """
                    CREATE TABLE events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        received_at TEXT NOT NULL,
                        charger_id TEXT NOT NULL,
                        action TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        transaction_id INTEGER,
                        id_tag TEXT,
                        charger_timestamp TEXT,
                        payload_json TEXT NOT NULL
                    );
                    CREATE INDEX idx_events_charger_time
                        ON events (charger_id, received_at);
                    CREATE INDEX idx_events_transaction
                        ON events (transaction_id);
                    CREATE INDEX idx_events_card
                        ON events (id_tag);

                    CREATE TABLE runtime_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        occurred_at TEXT NOT NULL,
                        event TEXT NOT NULL,
                        charger_id TEXT,
                        details_json TEXT
                    );
                    CREATE INDEX idx_runtime_events_time
                        ON runtime_events (occurred_at);
                    CREATE INDEX idx_runtime_events_charger_time
                        ON runtime_events (charger_id, occurred_at);
                    """
                )
                connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

    def record_ocpp(
        self,
        charger_id: str,
        action: str,
        payload: dict[str, Any],
        *,
        direction: str = "in",
        transaction_id: int | None = None,
    ) -> None:
        if transaction_id is None:
            raw_transaction_id = payload.get("transaction_id")
            if raw_transaction_id is not None:
                try:
                    transaction_id = int(raw_transaction_id)
                except (TypeError, ValueError):
                    transaction_id = None

        id_tag = payload.get("id_tag")
        charger_timestamp = payload.get("timestamp")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO events (
                    received_at, charger_id, action, direction,
                    transaction_id, id_tag, charger_timestamp, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    utc_now_iso(),
                    charger_id,
                    action,
                    direction,
                    transaction_id,
                    str(id_tag) if id_tag is not None else None,
                    str(charger_timestamp) if charger_timestamp is not None else None,
                    json.dumps(payload, sort_keys=True, ensure_ascii=False),
                ),
            )

    def record_runtime(
        self,
        event: str,
        *,
        charger_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runtime_events (occurred_at, event, charger_id, details_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    utc_now_iso(),
                    event,
                    charger_id,
                    json.dumps(details, sort_keys=True, ensure_ascii=False)
                    if details is not None
                    else None,
                ),
            )
