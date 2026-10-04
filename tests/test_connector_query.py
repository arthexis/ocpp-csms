from ocpp_csms.connector_query import physical_connector_ids
from ocpp_csms.events import EventStore
from ocpp_csms.server import CSMSServer
from ocpp_csms.transactions import TransactionArchive


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
