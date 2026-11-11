from ocpp_csms.connector_query import physical_connector_ids
from ocpp_csms.evidence.store import EventStore
from ocpp_csms.server import CSMSServer
from ocpp_csms.transactions.archive import TransactionArchive


def record_status(store, charger, connector):
    store.record_connector_status(
        charger,
        {
            "connector_id": connector,
            "status": "Available",
            "error_code": "NoError",
            "timestamp": f"2026-10-03T20:00:{connector:02d}Z",
        },
    )


def test_physical_connector_ids_are_sorted_scoped_and_exclude_zero(tmp_path):
    store = EventStore(tmp_path)
    record_status(store, "charger-a", 2)
    record_status(store, "charger-a", 0)
    record_status(store, "charger-a", 1)
    record_status(store, "charger-b", 3)

    assert physical_connector_ids(tmp_path, "charger-a") == [1, 2]
    assert physical_connector_ids(tmp_path, "charger-b") == [3]
    assert physical_connector_ids(tmp_path, "unknown") == []


def test_server_registry_exposes_known_physical_connector_ids(tmp_path):
    events = EventStore(tmp_path)
    record_status(events, "charger-a", 1)
    record_status(events, "charger-a", 2)
    server = CSMSServer(
        host="127.0.0.1",
        port=9000,
        transactions=TransactionArchive(tmp_path),
        events=events,
    )

    assert server.physical_connector_ids("charger-a") == [1, 2]


def test_missing_connector_database_is_not_created(tmp_path):
    import sqlite3
    import pytest
    from ocpp_csms.evidence.store import DATABASE_FILENAME

    path = tmp_path / DATABASE_FILENAME
    assert not path.exists()
    with pytest.raises(sqlite3.OperationalError):
        physical_connector_ids(tmp_path, "unknown")
    assert not path.exists()


def test_connector_reader_uses_ro_uri_and_closes_on_success_and_error(
    tmp_path, monkeypatch
):
    import sqlite3
    import pytest

    store = EventStore(tmp_path)
    record_status(store, "charger-a", 2)
    original_connect = sqlite3.connect
    opened = []
    should_fail = [False]

    class TrackedConnection(sqlite3.Connection):
        def execute(self, query, *args, **kwargs):
            if should_fail[0] and "SELECT connector_id" in query:
                raise sqlite3.OperationalError("injected read failure")
            return super().execute(query, *args, **kwargs)

    def tracked_connect(database, *args, **kwargs):
        assert kwargs.get("uri") is True
        assert str(database).startswith("file:")
        assert "mode=ro" in str(database)
        connection = original_connect(
            database, *args, factory=TrackedConnection, **kwargs
        )
        opened.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", tracked_connect)
    assert physical_connector_ids(tmp_path, "charger-a") == [2]
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].execute("SELECT 1")

    should_fail[0] = True
    with pytest.raises(sqlite3.OperationalError, match="injected read failure"):
        physical_connector_ids(tmp_path, "charger-a")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[1].execute("SELECT 1")
