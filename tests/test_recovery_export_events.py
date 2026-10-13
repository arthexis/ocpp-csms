from __future__ import annotations

import sqlite3

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.export_contract import export_contract
from ocpp_csms.schema import DATABASE_FILENAME


def _transaction(store, transaction_id=17):
    store.record_transaction_start(transaction_id, "cp-a", {
        "connector_id": 1, "id_tag": "rfid", "meter_start": 100,
        "timestamp": "2026-01-01T00:00:00Z",
    })


def test_inference_appends_one_exportable_internal_event(tmp_path):
    store = EventStore(tmp_path)
    _transaction(store)
    assert store.infer_transaction_stop(17)
    assert not store.infer_transaction_stop(17)
    page = export_contract(tmp_path)["data"]
    assert len(page["events"]) == 1
    event = page["events"][0]
    assert event["kind"] == "recovery"
    assert event["transaction_id"] == 17
    assert event["previous_state"] == "open"
    assert event["new_state"] == "inferred_stopped"
    assert event["reason"] == "disconnected_timeout"
    with sqlite3.connect(tmp_path / DATABASE_FILENAME) as db:
        assert db.execute("SELECT count(*) FROM transaction_recoveries").fetchone()[0] == 1


def test_status_ending_appends_correlated_event_once(tmp_path):
    store = EventStore(tmp_path)
    _transaction(store)
    payload = {"connector_id": 1, "status": "Available", "timestamp": "2026-01-01T00:01:00Z"}
    assert store.record_connector_status("cp-a", payload)
    assert store.record_connector_status("cp-a", payload)
    events = export_contract(tmp_path)["data"]["events"]
    assert len(events) == 1
    assert events[0]["kind"] == "recovery"
    assert events[0]["transaction_id"] == 17
    assert events[0]["reason"] == "connector_status"
