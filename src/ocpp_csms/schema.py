from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

DATABASE_FILENAME = "ocpp-csms.sqlite3"
CURRENT_SCHEMA_VERSION = 2

_SCHEMA_1_SQL = """
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

_SCHEMA_2_ADDITIONS_SQL = """
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

CURRENT_SCHEMA_SQL = _SCHEMA_1_SQL + _SCHEMA_2_ADDITIONS_SQL


@dataclass(frozen=True)
class SchemaInfo:
    path: Path
    exists: bool
    version: int | None


def database_path(data_dir: str | Path) -> Path:
    return Path(data_dir).expanduser() / DATABASE_FILENAME


def inspect_schema(data_dir: str | Path) -> SchemaInfo:
    """Inspect SQLite schema metadata without creating or modifying the database."""
    path = database_path(data_dir)
    if not path.exists():
        return SchemaInfo(path=path, exists=False, version=None)
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    finally:
        connection.close()
    return SchemaInfo(path=path, exists=True, version=version)


def create_current_schema(data_dir: str | Path) -> SchemaInfo:
    """Create a new database directly at the current schema version."""
    path = database_path(data_dir)
    if path.exists():
        raise RuntimeError(f"events database already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(CURRENT_SCHEMA_SQL)
        connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")
    return SchemaInfo(path=path, exists=True, version=CURRENT_SCHEMA_VERSION)


def require_supported_schema(info: SchemaInfo) -> int:
    if not info.exists or info.version is None:
        raise RuntimeError("events database does not exist")
    if info.version > CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"events database schema {info.version} is newer than supported {CURRENT_SCHEMA_VERSION}"
        )
    return info.version
