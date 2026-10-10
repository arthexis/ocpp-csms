"""EmergencyStop is derived per connector, including with no transaction."""
import sqlite3

from ocpp_csms.evidence.diagnostics import events_between, format_events
from ocpp_csms.evidence.contracts import events_contract
from ocpp_csms.evidence.store import EventStore
from ocpp_csms.schema import database_path, inspect_schema, upgrade_schema
from ocpp_csms.status import appliance_status, format_status
from ocpp_csms.status_contract import status_contract


CP = "GSCSC0824110052X0124"
FAULT = {"connector_id": 1, "status": "Faulted",
         "error_code": "InternalError", "info": "EmergencyStop",
         "timestamp": "2026-10-03T20:35:43.962Z"}


def test_emergency_without_transaction_and_connector_isolation(tmp_path):
    store = EventStore(tmp_path)
    store.record_connector_status(CP, FAULT)
    store.record_connector_status(CP, {
        "connector_id": 0, "status": "Available",
        "error_code": "NoError", "timestamp": "2026-10-03T20:36:00Z"})
    charger = appliance_status(tmp_path)["chargers"][0]
    assert charger.status == "Faulted"
    assert charger.derived_status == "EmergencyStop"
    assert charger.transaction_id is None
    assert charger.connectors[1].derived_status == "EmergencyStop"
    report = status_contract(appliance_status(tmp_path))["data"]["chargers"][0]
    assert report["status"] == "Faulted"
    assert report["derived_status"] == "EmergencyStop"
    assert report["connectors"][1]["info"] == "EmergencyStop"
    assert "EmergencyStop" in format_status(appliance_status(tmp_path), charger_id=CP)
    assert "EmergencyStop" in format_status(appliance_status(tmp_path))


def test_stale_and_connector_specific_recovery(tmp_path):
    store = EventStore(tmp_path)
    store.record_connector_status(CP, FAULT)
    assert store.record_connector_status(CP, {
        "connector_id": 1, "status": "Available",
        "error_code": "NoError", "timestamp": "2026-10-03T20:30:00Z"}) is False
    assert appliance_status(tmp_path)["chargers"][0].derived_status == "EmergencyStop"
    assert store.record_connector_status(CP, {
        "connector_id": 1, "status": "Available",
        "error_code": "NoError", "timestamp": "2026-10-03T20:37:00Z"}) is True
    charger = appliance_status(tmp_path)["chargers"][0]
    assert charger.connectors[0].derived_status == "Available"
    assert charger.connectors[0].info is None


def test_generic_internal_error_remains_generic(tmp_path):
    store = EventStore(tmp_path)
    store.record_connector_status(CP, {**FAULT, "info": "OtherFailure"})
    assert appliance_status(tmp_path)["chargers"][0].derived_status == "Faulted"


def test_event_history_shows_emergency_without_transaction(tmp_path):
    store = EventStore(tmp_path)
    store.record_ocpp(CP, "StatusNotification", FAULT)
    rows = events_between(tmp_path, charger_id=CP)
    event = events_contract(rows)["data"]["events"][0]
    assert event["derived_status"] == "EmergencyStop"
    assert event["status"] == "Faulted"
    assert event["info"] == "EmergencyStop"
    assert "EmergencyStop" in format_events(rows)


def test_upgrade_v5_preserves_connector_status(tmp_path):
    store = EventStore(tmp_path)
    store.record_connector_status(CP, FAULT)
    db = database_path(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE old_connector AS SELECT charger_id, connector_id, status, error_code, event_timestamp, received_at FROM connector_status")
        conn.execute("DROP TABLE connector_status")
        conn.execute("ALTER TABLE old_connector RENAME TO connector_status")
        conn.execute("PRAGMA user_version = 5")
    assert upgrade_schema(tmp_path).version == 6
    assert db.with_name(db.name + ".schema-5.bak").exists()
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT status, info FROM connector_status WHERE charger_id=?", (CP,)).fetchone() == ("Faulted", None)
