from __future__ import annotations

import sqlite3
from contextlib import closing
import uuid
from dataclasses import dataclass
from pathlib import Path

DATABASE_FILENAME = "ocpp-csms.sqlite3"
CURRENT_SCHEMA_VERSION = 8

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

_SCHEMA_3_ADDITIONS_SQL = """
CREATE TABLE IF NOT EXISTS rfid_lists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    charger_id TEXT NOT NULL,
    list_version INTEGER NOT NULL,
    sent_at TEXT NOT NULL,
    source_file TEXT,
    list_hash TEXT NOT NULL,
    verified_version INTEGER
);
CREATE INDEX IF NOT EXISTS idx_rfid_lists_charger_sent
    ON rfid_lists (charger_id, sent_at);
CREATE INDEX IF NOT EXISTS idx_rfid_lists_charger_version
    ON rfid_lists (charger_id, list_version);

CREATE TABLE IF NOT EXISTS rfid_list_entries (
    list_id INTEGER NOT NULL,
    rfid TEXT NOT NULL,
    name TEXT,
    enabled INTEGER NOT NULL,
    PRIMARY KEY (list_id, rfid),
    FOREIGN KEY (list_id) REFERENCES rfid_lists(id) ON DELETE CASCADE
);
"""

_SCHEMA_4_ADDITIONS_SQL = """
CREATE TABLE IF NOT EXISTS appliance_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_SCHEMA_5_ADDITIONS_SQL = """
CREATE TABLE IF NOT EXISTS transaction_recoveries (
    transaction_id INTEGER PRIMARY KEY,
    inferred_at TEXT NOT NULL,
    last_activity_at TEXT NOT NULL,
    last_meter_wh INTEGER,
    reason TEXT NOT NULL,
    reconciled_at TEXT,
    FOREIGN KEY (transaction_id) REFERENCES transactions(transaction_id)
);
"""

_SCHEMA_6_ADDITIONS_SQL = """
ALTER TABLE connector_status ADD COLUMN info TEXT;
"""

_SCHEMA_7_ADDITIONS_SQL = """
CREATE TABLE IF NOT EXISTS start_identities (
    charger_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    transaction_id INTEGER NOT NULL UNIQUE,
    PRIMARY KEY (charger_id, payload_json),
    FOREIGN KEY (transaction_id) REFERENCES transactions(transaction_id)
);
CREATE TABLE IF NOT EXISTS transaction_allocator (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    last_id INTEGER NOT NULL CHECK (last_id BETWEEN 0 AND 2147483647)
);
INSERT OR IGNORE INTO transaction_allocator (singleton, last_id)
SELECT 1, COALESCE(MAX(transaction_id), 0) FROM transactions;
"""

_SCHEMA_8_ADDITIONS_SQL = """
CREATE INDEX IF NOT EXISTS idx_transactions_boundary
    ON transactions (charger_id, connector_id, start_received_at, transaction_id);

DROP VIEW IF EXISTS transaction_boundaries;
CREATE VIEW transaction_boundaries AS
WITH ordered AS (
    SELECT transaction_id,
           LEAD(start_received_at) OVER (
               PARTITION BY charger_id, connector_id
               ORDER BY start_received_at, transaction_id
           ) AS next_start_received_at
    FROM transactions
)
SELECT t.transaction_id, t.charger_id, t.connector_id,
       t.start_received_at, t.stop_received_at AS observed_stop_received_at,
       o.next_start_received_at,
       t.stop_received_at AS observed_stop_at
FROM transactions AS t
JOIN ordered AS o ON o.transaction_id = t.transaction_id;

DROP VIEW IF EXISTS transaction_summary;
DROP TABLE IF EXISTS transaction_recoveries;

CREATE VIEW transaction_summary AS
SELECT t.transaction_id, t.charger_id, t.connector_id, t.id_tag,
       t.started_at, t.stopped_at, t.state, t.meter_start, t.meter_stop,
       CASE WHEN t.meter_start IS NOT NULL AND t.meter_stop IS NOT NULL
                  AND t.meter_stop >= t.meter_start
            THEN t.meter_stop - t.meter_start END AS energy_wh,
       t.last_activity_at, t.start_received_at, t.stop_received_at,
       b.next_start_received_at, b.observed_stop_at
FROM transactions AS t
JOIN transaction_boundaries AS b ON b.transaction_id = t.transaction_id;
"""

CURRENT_SCHEMA_SQL = (_SCHEMA_1_SQL + _SCHEMA_2_ADDITIONS_SQL + _SCHEMA_3_ADDITIONS_SQL
                      + _SCHEMA_4_ADDITIONS_SQL + _SCHEMA_7_ADDITIONS_SQL + _SCHEMA_8_ADDITIONS_SQL).replace(
    'CREATE TABLE IF NOT EXISTS connector_status (',
    'CREATE TABLE IF NOT EXISTS connector_status (\n    info TEXT,', 1)
_SUPPORTED_UPGRADES = {1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 7, 7: 8}


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


def ensure_wal(data_dir: str | Path) -> None:
    """Enable persistent WAL mode; never treat a busy database as corruption."""
    path = database_path(data_dir)
    if not path.exists():
        raise RuntimeError(f"events database does not exist: {path}")
    try:
        with closing(sqlite3.connect(path, timeout=5)) as connection:
            mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()
            if mode is None or str(mode[0]).lower() != "wal":
                raise RuntimeError(f"cannot enable WAL for events database: {mode}")
    except sqlite3.OperationalError as exc:
        raise RuntimeError(f"events database WAL readiness failed: {exc}") from exc


def create_current_schema(data_dir: str | Path) -> SchemaInfo:
    """Create a new database directly at the current schema version."""
    path = database_path(data_dir)
    if path.exists():
        raise RuntimeError(f"events database already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(CURRENT_SCHEMA_SQL)
        connection.execute(
            "INSERT INTO appliance_metadata (key, value) VALUES ('source_id', ?)",
            (str(uuid.uuid4()),),
        )
        connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")
    ensure_wal(data_dir)
    return SchemaInfo(path=path, exists=True, version=CURRENT_SCHEMA_VERSION)


def require_supported_schema(info: SchemaInfo) -> int:
    if not info.exists or info.version is None:
        raise RuntimeError("events database does not exist")
    if info.version > CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"events database schema {info.version} is newer than supported {CURRENT_SCHEMA_VERSION}"
        )
    if info.version < CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"events database schema {info.version} is older than required {CURRENT_SCHEMA_VERSION}; explicit upgrade required"
        )
    return info.version


def can_upgrade_schema(info: SchemaInfo) -> bool:
    """Return whether this historical schema has a complete upgrade path."""
    if not info.exists or info.version is None:
        return False
    version = info.version
    seen: set[int] = set()
    while version < CURRENT_SCHEMA_VERSION and version not in seen:
        seen.add(version)
        next_version = _SUPPORTED_UPGRADES.get(version)
        if next_version is None or next_version <= version:
            return False
        version = next_version
    return version == CURRENT_SCHEMA_VERSION


def schema_backup_path(info: SchemaInfo) -> Path:
    if not info.exists or info.version is None:
        raise RuntimeError("events database does not exist")
    return info.path.with_name(f"{info.path.name}.schema-{info.version}.bak")


def _backup_database(info: SchemaInfo) -> Path:
    backup = schema_backup_path(info)
    if backup.exists():
        raise RuntimeError(f"schema backup already exists: {backup}")
    source = sqlite3.connect(f"file:{info.path}?mode=ro", uri=True)
    try:
        with closing(sqlite3.connect(backup)) as destination:
            source.backup(destination)
    finally:
        source.close()
    return backup


def upgrade_schema(data_dir: str | Path) -> SchemaInfo:
    """Upgrade a known historical schema after first making a SQLite-safe backup."""
    info = inspect_schema(data_dir)
    if not info.exists or info.version is None:
        raise RuntimeError("events database does not exist")
    if info.version == CURRENT_SCHEMA_VERSION:
        return info
    if info.version > CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"events database schema {info.version} is newer than supported {CURRENT_SCHEMA_VERSION}"
        )
    if not can_upgrade_schema(info):
        raise RuntimeError(
            f"no supported schema upgrade from {info.version} to {CURRENT_SCHEMA_VERSION}"
        )

    _backup_database(info)
    try:
        with closing(sqlite3.connect(info.path)) as connection, connection:
            if info.version == 1:
                connection.executescript(_SCHEMA_2_ADDITIONS_SQL)
                connection.execute("PRAGMA user_version = 2")
                connection.commit()
                info = SchemaInfo(path=info.path, exists=True, version=2)
            if info.version == 2:
                connection.executescript(_SCHEMA_3_ADDITIONS_SQL)
                connection.execute("PRAGMA user_version = 3")
                connection.commit()
                info = SchemaInfo(path=info.path, exists=True, version=3)
            if info.version == 3:
                connection.executescript(_SCHEMA_4_ADDITIONS_SQL)
                connection.execute(
                    "INSERT OR IGNORE INTO appliance_metadata (key, value) VALUES ('source_id', ?)",
                    (str(uuid.uuid4()),),
                )
                connection.execute("PRAGMA user_version = 4")
                connection.commit()
                info = SchemaInfo(path=info.path, exists=True, version=4)
            if info.version == 4:
                connection.executescript(_SCHEMA_5_ADDITIONS_SQL)
                info = SchemaInfo(path=info.path, exists=True, version=5)
            if info.version == 5:
                connection.executescript(_SCHEMA_6_ADDITIONS_SQL)
                info = SchemaInfo(path=info.path, exists=True, version=6)
            if info.version == 6:
                connection.executescript(_SCHEMA_7_ADDITIONS_SQL)
                info = SchemaInfo(path=info.path, exists=True, version=7)
            if info.version == 7:
                connection.executescript(_SCHEMA_8_ADDITIONS_SQL)
            connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")
    except Exception:
        # Keep the backup as evidence/recovery material if the upgrade fails.
        raise

    upgraded = inspect_schema(data_dir)
    require_supported_schema(upgraded)
    return upgraded


def source_id(data_dir: str | Path) -> str:
    """Return the stable identity of this CSMS data store."""
    info = inspect_schema(data_dir)
    require_supported_schema(info)
    connection = sqlite3.connect(f"file:{info.path}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT value FROM appliance_metadata WHERE key = 'source_id'"
        ).fetchone()
    finally:
        connection.close()
    if row is None or not row[0]:
        raise RuntimeError("CSMS data store is missing source identity")
    return str(row[0])
