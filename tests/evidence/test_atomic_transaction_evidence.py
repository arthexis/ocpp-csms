"""Chunk 2: SQLite-authoritative, additive transaction evidence."""
import json

import pytest

from ocpp_csms.evidence.store import EventStore


START = {"connector_id": 1, "id_tag": "card-a", "meter_start": 100, "timestamp": "2026-10-03T10:00:00Z"}


def rows(store):
    with store._connect() as connection:
        events = connection.execute(
            "SELECT charger_id, action, transaction_id, payload_json FROM events ORDER BY rowid"
        ).fetchall()
        transactions = connection.execute(
            "SELECT transaction_id, charger_id, meter_stop, stopped_at FROM transactions ORDER BY transaction_id"
        ).fetchall()
    return events, transactions


def test_orphan_meter_and_stop_are_events_not_transactions(tmp_path):
    store = EventStore(tmp_path)
    store.commit_transaction_evidence("cp-a", "MeterValues", {
        "transaction_id": 999, "connector_id": 1, "meter_value": []
    })
    store.commit_transaction_evidence("cp-a", "StopTransaction", {
        "transaction_id": 999, "meter_stop": 150, "timestamp": "2026-10-03T10:10:00Z"
    })
    events, transactions = rows(store)
    assert [event[1] for event in events] == ["MeterValues", "StopTransaction"]
    assert [event[2] for event in events] == [999, 999]
    assert transactions == []


def test_cross_charge_point_stop_does_not_mutate_transaction(tmp_path):
    store = EventStore(tmp_path)
    transaction_id = store.commit_start("cp-a", START)
    store.commit_transaction_evidence("cp-b", "StopTransaction", {
        "transaction_id": transaction_id, "meter_stop": 999,
        "timestamp": "2026-10-03T10:10:00Z"
    })
    events, transactions = rows(store)
    assert events[-1][0] == "cp-b"
    assert transactions[0][2] is None


def test_conflicting_stops_preserve_first_and_both_events(tmp_path):
    store = EventStore(tmp_path)
    transaction_id = store.commit_start("cp-a", START)
    stop = {"transaction_id": transaction_id, "meter_stop": 150, "timestamp": "2026-10-03T10:10:00Z"}
    store.commit_transaction_evidence("cp-a", "StopTransaction", stop)
    store.commit_transaction_evidence("cp-a", "StopTransaction", {**stop, "meter_stop": 999})
    events, transactions = rows(store)
    assert transactions[0][2] == 150
    assert [json.loads(event[3])["meter_stop"] for event in events if event[1] == "StopTransaction"] == [150, 999]


def test_exact_meter_retry_preserves_each_receipt(tmp_path):
    store = EventStore(tmp_path)
    payload = {"transaction_id": 404, "connector_id": 1, "meter_value": []}
    store.commit_transaction_evidence("cp-a", "MeterValues", payload)
    store.commit_transaction_evidence("cp-a", "MeterValues", payload)
    events, transactions = rows(store)
    assert len(events) == 2
    assert transactions == []


def test_failed_stop_state_update_rolls_back_event(tmp_path):
    store = EventStore(tmp_path)
    transaction_id = store.commit_start("cp-a", START)
    before = rows(store)
    with pytest.raises((TypeError, ValueError)):
        store.commit_transaction_evidence("cp-a", "StopTransaction", {
            "transaction_id": transaction_id, "meter_stop": "invalid",
            "timestamp": "2026-10-03T10:10:00Z"
        })
    assert rows(store) == before
