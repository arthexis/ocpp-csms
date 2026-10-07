import sqlite3

import pytest

from ocpp_csms.events import EventStore
from ocpp_csms.schema import (
    CURRENT_SCHEMA_VERSION,
    DATABASE_FILENAME,
    can_upgrade_schema,
    create_current_schema,
    inspect_schema,
    require_supported_schema,
    schema_backup_path,
    upgrade_schema,
)


def test_missing_database_inspection_is_read_only(tmp_path):
    data_dir = tmp_path / "missing"

    info = inspect_schema(data_dir)

    assert info.exists is False
    assert info.version is None
    assert info.path == data_dir / DATABASE_FILENAME
    assert not data_dir.exists()


def test_current_schema_is_created_directly_at_version_three(tmp_path):
    info = create_current_schema(tmp_path)

    assert info.exists is True
    assert info.version == CURRENT_SCHEMA_VERSION == 3
    with sqlite3.connect(info.path) as connection:
        objects = {
            (kind, name)
            for kind, name in connection.execute(
                "SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            )
        }
        version = connection.execute("PRAGMA user_version").fetchone()[0]

    assert version == 3
    assert ("table", "events") in objects
    assert ("table", "runtime_events") in objects
    assert ("table", "transactions") in objects
    assert ("table", "connector_status") in objects
    assert ("table", "rfid_lists") in objects
    assert ("table", "rfid_list_entries") in objects
    assert ("view", "transaction_summary") in objects


def test_event_store_creates_new_database_at_current_schema(tmp_path):
    EventStore(tmp_path)

    info = inspect_schema(tmp_path)
    assert info.exists is True
    assert info.version == CURRENT_SCHEMA_VERSION


def test_inspection_reports_newer_schema_without_modifying_it(tmp_path):
    path = tmp_path / DATABASE_FILENAME
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 99")

    info = inspect_schema(tmp_path)

    assert info.version == 99
    with pytest.raises(RuntimeError, match="schema 99 is newer than supported 3"):
        require_supported_schema(info)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99


def test_event_store_refuses_newer_schema(tmp_path):
    path = tmp_path / DATABASE_FILENAME
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 99")

    with pytest.raises(RuntimeError, match="schema 99 is newer than supported 2"):
        EventStore(tmp_path)


def test_event_store_refuses_older_schema_without_mutating_it(tmp_path, schema_one):
    with pytest.raises(RuntimeError, match="schema 1 is older than required 3; explicit upgrade required"):
        EventStore(tmp_path)

    with sqlite3.connect(schema_one) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute(
            "SELECT event, charger_id FROM runtime_events"
        ).fetchone() == ("legacy_evidence", "charger-a")
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='transactions'"
        ).fetchone()[0] == 0


def test_schema_one_has_explicit_upgrade_path(tmp_path, schema_one):
    info = inspect_schema(tmp_path)

    assert info.path == schema_one
    assert can_upgrade_schema(info) is True
    assert schema_backup_path(info).name == f"{DATABASE_FILENAME}.schema-1.bak"


def test_upgrade_schema_one_to_three_preserves_evidence_and_creates_backup(tmp_path, schema_one):
    info = inspect_schema(tmp_path)
    backup = schema_backup_path(info)

    upgraded = upgrade_schema(tmp_path)

    assert upgraded.version == 3
    assert backup.exists()
    with sqlite3.connect(schema_one) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute(
            "SELECT event, charger_id FROM runtime_events"
        ).fetchone() == ("legacy_evidence", "charger-a")
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='transactions'"
        ).fetchone()[0] == 1
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute(
            "SELECT event, charger_id FROM runtime_events"
        ).fetchone() == ("legacy_evidence", "charger-a")
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='transactions'"
        ).fetchone()[0] == 0


def test_upgrade_current_schema_is_noop_without_backup(tmp_path):
    info = create_current_schema(tmp_path)

    upgraded = upgrade_schema(tmp_path)

    assert upgraded == info
    assert not schema_backup_path(info).exists()


def test_upgrade_refuses_unversioned_database(tmp_path):
    path = tmp_path / DATABASE_FILENAME
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE legacy (value TEXT)")

    info = inspect_schema(tmp_path)
    assert info.version == 0
    assert can_upgrade_schema(info) is False
    with pytest.raises(RuntimeError, match="no supported schema upgrade from 0 to 3"):
        upgrade_schema(tmp_path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='legacy'").fetchone() == ("legacy",)


def test_upgrade_refuses_to_overwrite_existing_backup(tmp_path, schema_one):
    info = inspect_schema(tmp_path)
    backup = schema_backup_path(info)
    backup.write_text("keep me")

    with pytest.raises(RuntimeError, match="schema backup already exists"):
        upgrade_schema(tmp_path)

    assert backup.read_text() == "keep me"
    assert inspect_schema(tmp_path).version == 1


def test_current_schema_creation_refuses_existing_database(tmp_path):
    path = tmp_path / DATABASE_FILENAME
    path.touch()

    with pytest.raises(RuntimeError, match="already exists"):
        create_current_schema(tmp_path)


def test_schema_two_upgrades_to_three_with_rfid_history_tables(tmp_path):
    path = tmp_path / DATABASE_FILENAME
    with sqlite3.connect(path) as connection:
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
            CREATE TABLE runtime_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                occurred_at TEXT NOT NULL,
                event TEXT NOT NULL,
                charger_id TEXT,
                details_json TEXT
            );
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
            CREATE TABLE connector_status (
                charger_id TEXT NOT NULL,
                connector_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                error_code TEXT,
                event_timestamp TEXT NOT NULL,
                received_at TEXT NOT NULL,
                PRIMARY KEY (charger_id, connector_id)
            );
            PRAGMA user_version = 2;
            """
        )

    upgraded = upgrade_schema(tmp_path)

    assert upgraded.version == 3
    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "rfid_lists" in tables
    assert "rfid_list_entries" in tables
