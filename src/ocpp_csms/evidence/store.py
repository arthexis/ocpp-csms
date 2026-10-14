from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ocpp_csms.schema import (
    CURRENT_SCHEMA_VERSION,
    DATABASE_FILENAME,
    create_current_schema,
    inspect_schema,
    require_supported_schema,
)
from ocpp_csms.time import utc_now_iso


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
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        schema = inspect_schema(self.data_dir)
        if not schema.exists:
            create_current_schema(self.data_dir)
            return
        require_supported_schema(schema)

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

    def commit_start(self, charger_id: str, payload: dict[str, Any]) -> int:
        """Commit exact retry identity and every received StartTransaction atomically."""
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        now = utc_now_iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT transaction_id FROM start_identities WHERE charger_id = ? AND payload_json = ?",
                (charger_id, canonical),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    "SELECT last_id FROM transaction_allocator WHERE singleton = 1"
                ).fetchone()
                if row is None:
                    raise RuntimeError("SQLite transaction allocator is missing")
                next_id = int(row[0]) + 1
                if next_id > 2147483647:
                    raise OverflowError("OCPP transaction ID space exhausted")
                connection.execute(
                    "UPDATE transaction_allocator SET last_id = ? WHERE singleton = 1",
                    (next_id,),
                )
                connection.execute(
                    """INSERT INTO transactions (
                        transaction_id, charger_id, connector_id, id_tag,
                        meter_start, started_at, start_received_at, state, last_activity_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)""",
                    (next_id, charger_id, int(payload["connector_id"]),
                     str(payload["id_tag"]), int(payload["meter_start"]),
                     str(payload["timestamp"]), now, now),
                )
                connection.execute(
                    "INSERT INTO start_identities (charger_id, payload_json, transaction_id) VALUES (?, ?, ?)",
                    (charger_id, canonical, next_id),
                )
                transaction_id = next_id
            else:
                transaction_id = int(row[0])
            connection.execute(
                """INSERT INTO events (
                    received_at, charger_id, action, direction, transaction_id,
                    id_tag, charger_timestamp, payload_json
                ) VALUES (?, ?, 'StartTransaction', 'in', ?, ?, ?, ?)""",
                (now, charger_id, transaction_id, str(payload["id_tag"]),
                 str(payload["timestamp"]), json.dumps(payload, sort_keys=True, ensure_ascii=False)),
            )
        return transaction_id

    def find_recent_start(
        self,
        charger_id: str,
        payload: dict[str, Any],
        *,
        window_seconds: int = 60,
    ) -> int | None:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT transaction_id FROM start_identities WHERE charger_id = ? AND payload_json = ?",
                (charger_id, canonical),
            ).fetchone()
        return int(row[0]) if row else None

    def record_transaction_start(
        self,
        transaction_id: int,
        charger_id: str,
        payload: dict[str, Any],
    ) -> None:
        now = utc_now_iso()
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
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
            connection.execute(
                "INSERT INTO start_identities (charger_id, payload_json, transaction_id) VALUES (?, ?, ?)",
                (charger_id, canonical, int(transaction_id)),
            )
            connection.execute(
                "UPDATE transaction_allocator SET last_id = MAX(last_id, ?) WHERE singleton = 1",
                (int(transaction_id),),
            )

    def record_transaction_activity(self, transaction_id: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE transactions SET last_activity_at = ? WHERE transaction_id = ?",
                (utc_now_iso(), int(transaction_id)),
            )

    def commit_transaction_evidence(
        self, charger_id: str, action: str, payload: dict[str, Any]
    ) -> None:
        """Persist incoming evidence before acknowledgment, without inventing starts."""
        if action not in {"MeterValues", "StopTransaction"}:
            raise ValueError("unsupported transaction evidence")
        transaction_id = int(payload["transaction_id"]) if payload.get("transaction_id") is not None else None
        now = utc_now_iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO events (
                    received_at, charger_id, action, direction, transaction_id,
                    id_tag, charger_timestamp, payload_json
                ) VALUES (?, ?, ?, 'in', ?, ?, ?, ?)""",
                (now, charger_id, action, transaction_id,
                 str(payload["id_tag"]) if payload.get("id_tag") is not None else None,
                 str(payload["timestamp"]) if payload.get("timestamp") is not None else None,
                 json.dumps(payload, sort_keys=True, ensure_ascii=False)),
            )
            if transaction_id is None:
                return
            if action == "MeterValues":
                connection.execute(
                    """UPDATE transactions SET last_activity_at = ?
                    WHERE transaction_id = ? AND charger_id = ?""",
                    (now, transaction_id, charger_id),
                )
            else:
                connection.execute(
                    """UPDATE transactions
                    SET meter_stop = ?, stopped_at = ?, stop_received_at = ?,
                        state = 'stopped', last_activity_at = ?
                    WHERE transaction_id = ? AND charger_id = ?
                      AND stop_received_at IS NULL""",
                    (int(payload["meter_stop"]), str(payload["timestamp"]), now, now,
                     transaction_id, charger_id),
                )

    def record_transaction_stop(self, charger_id: str, payload: dict[str, Any]) -> None:
        self.commit_transaction_evidence(charger_id, "StopTransaction", payload)

    def infer_transaction_stop(
        self, transaction_id: int, *, reason: str = "disconnected_timeout",
        last_meter_wh: int | None = None,
    ) -> bool:
        """Mark an inactive transaction as inferred, never invent a StopTransaction."""
        if reason != "disconnected_timeout":
            raise ValueError("unsupported recovery reason")
        now = utc_now_iso()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state, last_activity_at FROM transactions WHERE transaction_id = ?",
                (int(transaction_id),),
            ).fetchone()
            if row is None or row[0] not in {"open", "recovered"}:
                return False
            connection.execute(
                "UPDATE transactions SET state = 'inferred_stopped' WHERE transaction_id = ? AND state IN ('open', 'recovered')",
                (int(transaction_id),),
            )
            connection.execute(
                """INSERT INTO transaction_recoveries (
                    transaction_id, inferred_at, last_activity_at, last_meter_wh, reason
                ) VALUES (?, ?, ?, ?, ?)""",
                (int(transaction_id), now, str(row[1]), last_meter_wh, reason),
            )
        return True

    def record_connector_status(self, charger_id: str, payload: dict[str, Any]) -> bool:
        connector_id = int(payload["connector_id"])
        status = str(payload["status"])
        received_at = utc_now_iso()
        charger_timestamp = payload.get("timestamp")
        event_timestamp = str(charger_timestamp or received_at)

        with self._connect() as connection:
            row = connection.execute(
                "SELECT event_timestamp FROM connector_status WHERE charger_id = ? AND connector_id = ?",
                (charger_id, connector_id),
            ).fetchone()
            if row and _timestamp(event_timestamp) < _timestamp(str(row[0])):
                return False

            connection.execute(
                """
                INSERT INTO connector_status (
                    charger_id, connector_id, status, error_code, event_timestamp, received_at, info
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(charger_id, connector_id) DO UPDATE SET
                    status = excluded.status,
                    error_code = excluded.error_code,
                    info = excluded.info,
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
                    str(payload['info']) if payload.get('info') is not None else None,
                ),
            )
            if status in {"Finishing", "Available"}:
                transaction = connection.execute(
                    """
                    SELECT transaction_id, started_at FROM transactions
                    WHERE charger_id = ? AND connector_id = ? AND state = 'open'
                    ORDER BY start_received_at DESC LIMIT 1
                    """,
                    (charger_id, connector_id),
                ).fetchone()
                if transaction:
                    belongs_to_session = True
                    if charger_timestamp is not None and transaction[1] is not None:
                        belongs_to_session = _timestamp(str(charger_timestamp)) >= _timestamp(str(transaction[1]))
                    if belongs_to_session:
                        connection.execute(
                            """
                            UPDATE transactions SET state = 'ended', last_activity_at = ?
                            WHERE transaction_id = ? AND state = 'open'
                            """,
                            (received_at, int(transaction[0])),
                        )
        return True


    def latest_rfid_list_version(self, charger_id: str) -> int | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT MAX(list_version)
                FROM rfid_lists
                WHERE charger_id = ?
                """,
                (charger_id,),
            ).fetchone()
        if not row or row[0] is None:
            return None
        return int(row[0])

    def record_rfid_list(
        self,
        charger_id: str,
        *,
        list_version: int,
        entries: list[dict[str, Any]],
        source_file: str | None,
        list_hash: str,
        verified_version: int | None,
    ) -> int:
        """Persist an RFID list only after the charger accepted SendLocalList."""
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO rfid_lists (
                    charger_id, list_version, sent_at, source_file,
                    list_hash, verified_version
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    charger_id,
                    int(list_version),
                    utc_now_iso(),
                    source_file,
                    list_hash,
                    int(verified_version) if verified_version is not None else None,
                ),
            )
            list_id = int(cursor.lastrowid)
            for entry in entries:
                connection.execute(
                    """
                    INSERT INTO rfid_list_entries (
                        list_id, rfid, name, enabled
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        list_id,
                        str(entry["rfid"]),
                        str(entry["name"]) if entry.get("name") is not None else None,
                        1 if bool(entry.get("enabled", True)) else 0,
                    ),
                )
        return list_id

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
