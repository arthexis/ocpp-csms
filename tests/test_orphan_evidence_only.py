"""Regression contract: orphan evidence is not transaction state or allocator input."""

import sqlite3

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.export_contract import export_contract
from ocpp_csms.schema import DATABASE_FILENAME


def _rows(path, sql):
    with sqlite3.connect(path / DATABASE_FILENAME) as db:
        return db.execute(sql).fetchall()


def test_orphan_meter_and_stop_preserve_events_without_creating_transactions(tmp_path):
    store = EventStore(tmp_path)
    store.commit_transaction_evidence("charger-a", "MeterValues", {
        "connector_id": 1, "transaction_id": 99999, "meter_value": [],
    })
    store.commit_transaction_evidence("charger-a", "StopTransaction", {
        "transaction_id": 99999, "meter_stop": 150,
        "timestamp": "2026-10-03T10:10:00Z",
    })
    assert _rows(tmp_path, "SELECT transaction_id FROM transactions") == []
    assert _rows(tmp_path, "SELECT last_id FROM transaction_allocator") == [(0,)]
    page = export_contract(tmp_path, after=0, limit=10)["data"]
    assert [e["action"] for e in page["events"]] == ["MeterValues", "StopTransaction"]
    assert [e["transaction_id"] for e in page["events"]] == [99999, 99999]
    assert export_contract(tmp_path, after=0, limit=10)["data"]["events"] == page["events"]


def test_orphan_does_not_reserve_next_start_id_or_mutate_other_charger(tmp_path):
    store = EventStore(tmp_path)
    start = {"connector_id": 1, "id_tag": "CARD-A", "meter_start": 100,
             "timestamp": "2026-10-03T10:00:00Z"}
    first = store.commit_start("charger-a", start)
    store.commit_transaction_evidence("charger-b", "StopTransaction", {
        "transaction_id": first, "meter_stop": 150,
        "timestamp": "2026-10-03T10:10:00Z",
    })
    store.commit_transaction_evidence("charger-b", "MeterValues", {
        "connector_id": 1, "transaction_id": 99999, "meter_value": [],
    })
    assert _rows(tmp_path, "SELECT transaction_id, charger_id, state FROM transactions") == [
        (first, "charger-a", "open")
    ]
    assert _rows(tmp_path, "SELECT last_id FROM transaction_allocator") == [(first,)]
    second = store.commit_start("charger-b", dict(start, timestamp="2026-10-03T10:01:00Z"))
    assert second == first + 1
    assert store.commit_start("charger-a", start) == first


def test_genuine_late_stop_updates_only_matching_transaction(tmp_path):
    store = EventStore(tmp_path)
    first = store.commit_start("charger-a", {
        "connector_id": 1, "id_tag": "CARD-A", "meter_start": 100,
        "timestamp": "2026-10-03T10:00:00Z",
    })
    store.commit_start("charger-a", {
        "connector_id": 1, "id_tag": "CARD-B", "meter_start": 200,
        "timestamp": "2026-10-03T10:05:00Z",
    })
    store.commit_transaction_evidence("charger-a", "StopTransaction", {
        "transaction_id": first, "meter_stop": 180,
        "timestamp": "2026-10-03T10:04:00Z",
    })
    assert _rows(tmp_path, "SELECT state FROM transactions ORDER BY transaction_id") == [
        ("stopped",), ("open",)
    ]
    assert _rows(tmp_path, "SELECT last_id FROM transaction_allocator") == [(first + 1,)]
