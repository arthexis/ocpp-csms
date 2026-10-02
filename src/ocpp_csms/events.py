from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ocpp_csms.time import utc_now_iso

_SCHEMA_VERSION = 2

_DERIVED_SCHEMA = """
CREATE TABLE transactions (
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
CREATE INDEX idx_transactions_charger_state
    ON transactions (charger_id, state, start_received_at);
CREATE INDEX idx_transactions_connector_state
    ON transactions (charger_id, connector_id, state);

CREATE TABLE connector_status (
    charger_id TEXT NOT NULL,
    connector_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    error_code TEXT,
    event_timestamp TEXT NOT NULL,
    received_at TEXT NOT NULL,
    PRIMARY KEY (charger_id, connector_id)
);
CREATE INDEX idx_connector_status_received
    ON connector_status (charger_id, received_at);

CREATE VIEW transaction_summary AS
SELECT
    transaction_id,
    charger_id,
    connector_id,
    id_tag,
    started_at,
    stopped_at,
    state,
    meter_start,
    meter_stop,
    CASE
        WHEN meter_start IS NOT NULL AND meter_stop IS NOT NULL
            THEN meter_stop - meter_start
        ELSE NULL
    END AS energy_wh,
    last_activity_at
FROM transactions;
"""


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class EventStore:
    """SQLite evidence plus small derived operational state."""

    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir).expanduser()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "events.sqlite3"
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
                connection.executescript(_DERIVED_SCHEMA)
                connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            elif version == 1:
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

    def find_recent_start(
        self,
        charger_id: str,
        payload: dict[str, Any],
        *,
        window_seconds: int = 60,
    ) -> int | None:
        connector_id = int(payload["connector_id"])
        id_tag = str(payload["id_tag"])
        meter_start = int(payload["meter_start"])
        started_at = str(payload["timestamp"])
        threshold = _iso(datetime.now(timezone.utc) - timedelta(seconds=window_seconds))
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT transaction_id
                FROM transactions
                WHERE charger_id = ? AND connector_id = ? AND id_tag = ?
                  AND meter_start = ? AND started_at = ?
                  AND start_received_at >= ?
                ORDER BY start_received_at DESC
                LIMIT 1
                """,
                (charger_id, connector_id, id_tag, meter_start, started_at, threshold),
            ).fetchone()
        return int(row[0]) if row is not None else None

    def record_transaction_start(
        self,
        transaction_id: int,
        charger_id: str,
        payload: dict[str, Any],
    ) -> None:
        now = utc_now_iso()
        connector_id = int(payload["connector_id"])
        id_tag = str(payload["id_tag"])
        meter_start = int(payload["meter_start"])
        started_at = str(payload["timestamp"])
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO transactions (
                    transaction_id, charger_id, connector_id, id_tag,
                    meter_start, started_at, start_received_at,
                    state, last_activity_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)
                ON CONFLICT(transaction_id) DO UPDATE SET
                    charger_id = excluded.charger_id,
                    connector_id = excluded.connector_id,
                    id_tag = excluded.id_tag,
                    meter_start = excluded.meter_start,
                    started_at = excluded.started_at,
                    start_received_at = COALESCE(transactions.start_received_at, excluded.start_received_at),
                    state = CASE WHEN transactions.state = 'stopped' THEN 'stopped' ELSE 'open' END,
                    last_activity_at = excluded.last_activity_at
                """,
                (
                    transaction_id,
                    charger_id,
                    connector_id,
                    id_tag,
                    meter_start,
                    started_at,
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
        meter_stop = int(payload["meter_stop"])
        stopped_at = str(payload["timestamp"])
        now = utc_now_iso()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO transactions (
                    transaction_id, charger_id, meter_stop, stopped_at,
                    stop_received_at, state, last_activity_at
                ) VALUES (?, ?, ?, ?, ?, 'stopped', ?)
                ON CONFLICT(transaction_id) DO UPDATE SET
                    meter_stop = COALESCE(transactions.meter_stop, excluded.meter_stop),
                    stopped_at = COALESCE(transactions.stopped_at, excluded.stopped_at),
                    stop_received_at = COALESCE(transactions.stop_received_at, excluded.stop_received_at),
                    state = 'stopped',
                    last_activity_at = excluded.last_activity_at
                """,
                (transaction_id, charger_id, meter_stop, stopped_at, now, now),
            )

    def record_connector_status(self, charger_id: str, payload: dict[str, Any]) -> bool:
        connector_id = int(payload["connector_id"])
        status = str(payload["status"])
        error_code = payload.get("error_code")
        received_at = utc_now_iso()
        event_timestamp = str(payload.get("timestamp") or received_at)
        incoming_time = _parse_timestamp(event_timestamp)

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT status, error_code, event_timestamp
                FROM connector_status
                WHERE charger_id = ? AND connector_id = ?
                """,
                (charger_id, connector_id),
            ).fetchone()
            if row is not None:
                stored_time = _parse_timestamp(str(row[2]))
                if incoming_time <= stored_time:
                    return False

            connection.execute(
                """
                INSERT INTO connector_status (
                    charger_id, connector_id, status, error_code,
                    event_timestamp, received_at
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
                    str(error_code) if error_code is not None else None,
                    event_timestamp,
                    received_at,
                ),
            )
            if status in {"Finishing", "Available"}:
                connection.execute(
                    """
                    UPDATE transactions
                    SET state = 'ended', last_activity_at = ?
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
                    json.dumps(details, sort_keys=True, ensure_ascii=False)
                    if details is not None
                    else None,
                ),
            )
