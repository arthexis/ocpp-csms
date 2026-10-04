import sqlite3

import pytest

from ocpp_csms.events import EventStore
from ocpp_csms.schema import (
    CURRENT_SCHEMA_VERSION,
    DATABASE_FILENAME,
    create_current_schema,
    inspect_schema,
    require_supported_schema,
)


def test_missing_database_inspection_is_read_only(tmp_path):
    data_dir = tmp_path / "missing"

    info = inspect_schema(data_dir)

    assert info.exists is False
    assert info.version is None
    assert info.path == data_dir / DATABASE_FILENAME
    assert not data_dir.exists()


def test_current_schema_is_created_directly_at_version_two(tmp_path):
    info = create_current_schema(tmp_path)

    assert info.exists is True
    assert info.version == CURRENT_SCHEMA_VERSION == 2
    with sqlite3.connect(info.path) as connection:
        objects = {
            (kind, name)
            for kind, name in connection.execute(
                "SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            )
        }
        version = connection.execute("PRAGMA user_version").fetchone()[0]

    assert version == 2
    assert ("table", "events") in objects
    assert ("table", "runtime_events") in objects
    assert ("table", "transactions") in objects
    assert ("table", "connector_status") in objects
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
    with pytest.raises(RuntimeError, match="schema 99 is newer than supported 2"):
        require_supported_schema(info)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99


def test_event_store_refuses_newer_schema(tmp_path):
    path = tmp_path / DATABASE_FILENAME
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 99")

    with pytest.raises(RuntimeError, match="schema 99 is newer than supported 2"):
        EventStore(tmp_path)


def test_current_schema_creation_refuses_existing_database(tmp_path):
    path = tmp_path / DATABASE_FILENAME
    path.touch()

    with pytest.raises(RuntimeError, match="already exists"):
        create_current_schema(tmp_path)
