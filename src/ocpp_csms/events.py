from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ocpp_csms.time import utc_now_iso

DATABASE_FILENAME = "ocpp-csms.sqlite3"
_SCHEMA_VERSION = 2

_DERIVED_SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    transaction_id INTEGER PRIMARY KEY,
    charger_id TEXT NOT NULL,
    connector_id INTEGER,
    id_tag TEXT,
    meter_start INTEGER,
    started_at TEXT,
    start_received_at TEXT,
    meter_stop INTEGER,
    stopped_at TEXT,
    stop_received_at TEXT,
    state TEXT NOT NULL,
    last_activity_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transactions_charger_state
    ON transactions (charger_id, state, start_received_at);
CREATE INDEX IF NOT EXISTS idx_transactions_connector_state
    ON transactions (charger_id, connector_id, state);

CREATE TABLE IF NOT EXISTS connector_status (
    charger_id TEXT NOT NULL,
    connector_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    error_code TEXT,
    event_timestamp TEXT NOT NULL,
    received_at TEXT NOT NULL,
    PRIMARY KEY (charger_id, connector_id)
);

CREATE VIEW IF NOT EXISTS transaction_summary AS
SELECT
    transaction_id, charger_id, connector_id, id_tag,
    started_at, stopped_at, state, meter_start, meter_stop,
    CASE WHEN meter_start IS NOT NULL AND meter_stop IS NOT NULL
         THEN meter_stop - meter_start END AS energy_wh,
    last_activity_at
FROM transactions;
"""


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class EventStore:
    """SQLite evidence plus small derived operational state."""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir).expanduser()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / DATABASE_FILENAME
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=0)
        connection.execute("PRAGMA busy_timeout = 0")
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
            if version < 2:
                connection.executescript(_DERIVED_SCHEMA)
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
            try:
                transaction_id = int(payload["transaction_id"])
            except (KeyError, TypeError, ValueError):
                pass
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
                    utc_now_iso(), charger_id, action, direction, transaction_id,
                    str(id_tag) if id_tag is not None else None,
                    str(charger_timestamp) if charger_timestamp is not None else None,
                    json.dumps(payload, sort_keys=True, ensure_ascii=False),
                ),
            )

    def find_recent_start(
        self,
        charger_id: str,
        payload: dict[str, Any],
        *,
        window_seconds: int = 60,
    ) -> int | None:
        threshold = (
            datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
        ).isoformat(timespec="microseconds").replace("+00:00", "Z")
        values = (
            charger_id,
            int(payload["connector_id"]),
            str(payload["id_tag"]),
            int(payload["meter_start"]),
            str(payload["timestamp"]),
            threshold,
        )
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT transaction_id FROM transactions
                WHERE charger_id = ? AND connector_id = ? AND id_tag = ?
                  AND meter_start = ? AND started_at = ? AND start_received_at >= ?
                ORDER BY start_received_at DESC LIMIT 1
                """,
                values,
            ).fetchone()
        return int(row[0]) if row else None

    def record_transaction_start(
        self,
        transaction_id: int,
        charger_id: str,
        payload: dict[str, Any],
    ) -> None:
        now = utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO transactions (
                    transaction_id, charger_id, connector_id, id_tag,
                    meter_start, started_at, start_received_at, state, last_activity_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)
                """,
                (
                    transaction_id,
                    charger_id,
                    int(payload["connector_id"]),
                    str(payload["id_tag"]),
                    int(payload["meter_start"]),
                    str(payload["timestamp"]),
                    now,
                    now,
                ),
            )

    def record_transaction_activity(self, transaction_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE transactions SET last_activity_at = ? WHERE transaction_id = ?",
                (utc_now_iso(), int(transaction_id)),
            )

    def record_transaction_stop(self, charger_id: str, payload: dict[str, Any]) -> None:
        transaction_id = int(payload["transaction_id"])
        now = utc_now_iso()
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE transactions
                SET meter_stop = COALESCE(meter_stop, ?),
                    stopped_at = COALESCE(stopped_at, ?),
                    stop_received_at = COALESCE(stop_received_at, ?),
                    state = 'stopped', last_activity_at = ?
                WHERE transaction_id = ?
                """,
                (int(payload["meter_stop"]), str(payload["timestamp"]), now, now, transaction_id),
            ).rowcount
            if not updated:
                connection.execute(
                    """
                    INSERT INTO transactions (
                        transaction_id, charger_id, meter_stop, stopped_at,
                        stop_received_at, state, last_activity_at
                    ) VALUES (?, ?, ?, ?, ?, 'stopped', ?)
                    """,
                    (
                        transaction_id,
                        charger_id,
                        int(payload["meter_stop"]),
                        str(payload["timestamp"]),
                        now,
                        now,
                    ),
                )

    def record_connector_status(self, charger_id: str, payload: dict[str, Any]) -> bool:
        connector_id = int(payload["connector_id"])
        status = str(payload["status"])
        received_at = utc_now_iso()
        event_timestamp = str(payload.get("timestamp") or received_at)

        with self._connect() as connection:
            row = connection.execute(
                "SELECT event_timestamp FROM connector_status WHERE charger_id = ? AND connector_id = ?",
                (charger_id, connector_id),
            ).fetchone()
            if row and _timestamp(event_timestamp) <= _timestamp(str(row[0])):
                return False

            connection.execute(
                """
                INSERT INTO connector_status (
                    charger_id, connector_id, status, error_code, event_timestamp, received_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(charger_id, connector_id) DO UPDATE SET
                    status = excluded.status,
                    error_code = excluded.error_code,
                    event_timestamp = excluded.event_timestamp,
                    received_at = excluded.received_at
                """,
                (
                    charger_id,
                    connector_id,
                    status,
                    str(payload["error_code"]) if payload.get("error_code") is not None else None,
                    event_timestamp,
                    received_at,
                ),
            )
            if status in {"Finishing", "Available"}:
                connection.execute(
                    """
                    UPDATE transactions SET state = 'ended', last_activity_at = ?
                    WHERE charger_id = ? AND connector_id = ? AND state = 'open'
                    """,
                    (received_at, charger_id, connector_id),
                )
        return True

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
                    json.dumps(details, sort_keys=True, ensure_ascii=False) if details is not None else None,
                ),
            )
