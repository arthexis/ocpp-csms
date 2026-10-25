"""Current SQLite schema integration, without historical migration fixtures."""
import sqlite3

import pytest

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.schema import (
    CURRENT_SCHEMA_VERSION, DATABASE_FILENAME, create_current_schema,
    ensure_wal, inspect_schema, require_supported_schema, source_id,
)
from ocpp_csms.transactions.query import TransactionQuery


def test_missing_database_inspection_is_read_only(tmp_path):
    data_dir = tmp_path / "missing"
    info = inspect_schema(data_dir)
    assert not info.exists
    assert info.version is None
    assert info.path == data_dir / DATABASE_FILENAME
    assert not data_dir.exists()


def test_current_schema_executes_application_sql(tmp_path):
    store = EventStore(tmp_path)
    assert inspect_schema(tmp_path).version == CURRENT_SCHEMA_VERSION
    store.record_runtime("server_started", charger_id="cp-a")
    store.record_ocpp("cp-a", "Heartbeat", {}, direction="in")
    assert store.record_connector_status(
        "cp-a", {"connector_id": 1, "status": "Available", "error_code": "NoError"}
    )
    payload = {
        "connector_id": 1, "id_tag": "card-a",
        "meter_start": 100, "timestamp": "2026-10-01T00:00:00Z",
    }
    transaction_id = store.commit_start("cp-a", payload)
    assert store.commit_start("cp-a", payload) == transaction_id
    store.commit_transaction_evidence(
        "cp-a", "MeterValues", {"transaction_id": transaction_id, "meter_value": []}
    )
    store.commit_transaction_evidence(
        "cp-a", "StopTransaction",
        {"transaction_id": transaction_id, "meter_stop": 150, "timestamp": "2026-10-01T00:01:00Z"},
    )
    view = TransactionQuery(tmp_path).get(transaction_id)
    assert view is not None
    assert view.status == "stopped"
    assert view.record["energy_wh"] == 50
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_existing_database_is_not_replaced(tmp_path):
    info = create_current_schema(tmp_path)
    identity = source_id(tmp_path)
    assert source_id(tmp_path) == identity
    with pytest.raises(RuntimeError, match="already exists"):
        create_current_schema(tmp_path)
    assert info.path.exists()
    assert source_id(tmp_path) == identity


def test_new_databases_on_same_host_share_node_identity(tmp_path, monkeypatch):
    monkeypatch.setattr("ocpp_csms.node_identity.socket.gethostname", lambda: "gway-005")
    a = create_current_schema(tmp_path / "a").path.parent
    b = create_current_schema(tmp_path / "b").path.parent
    assert source_id(a) == source_id(b) == "gway-005"


def test_new_databases_on_different_hosts_have_distinct_node_identities(tmp_path, monkeypatch):
    monkeypatch.setattr("ocpp_csms.node_identity.socket.gethostname", lambda: "gway-005")
    a = create_current_schema(tmp_path / "a").path.parent
    monkeypatch.setattr("ocpp_csms.node_identity.socket.gethostname", lambda: "gway-006")
    b = create_current_schema(tmp_path / "b").path.parent
    assert source_id(a) == "gway-005"
    assert source_id(b) == "gway-006"


def test_newer_schema_is_rejected_without_modification(tmp_path):
    path = tmp_path / DATABASE_FILENAME
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 99")
    with pytest.raises(RuntimeError, match="newer than supported"):
        require_supported_schema(inspect_schema(tmp_path))
    with pytest.raises(RuntimeError, match="newer than supported"):
        EventStore(tmp_path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99


def test_wal_enabled_for_new_database_and_restarts(tmp_path):
    store = EventStore(tmp_path)
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    EventStore(tmp_path)
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_existing_current_database_converts_to_wal_without_replacing_identity(tmp_path):
    info = create_current_schema(tmp_path)
    identity = source_id(tmp_path)
    with sqlite3.connect(info.path) as connection:
        assert connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete"
    ensure_wal(tmp_path)
    assert source_id(tmp_path) == identity
    with sqlite3.connect(info.path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_wal_conversion_refuses_active_reader_without_destroying_database(tmp_path):
    info = create_current_schema(tmp_path)
    identity = source_id(tmp_path)
    with sqlite3.connect(info.path) as connection:
        connection.execute("PRAGMA journal_mode=DELETE")
    reader = sqlite3.connect(info.path)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM appliance_metadata").fetchall()
        with pytest.raises(RuntimeError, match="WAL readiness failed"):
            ensure_wal(tmp_path)
    finally:
        reader.close()
    assert source_id(tmp_path) == identity
    ensure_wal(tmp_path)
