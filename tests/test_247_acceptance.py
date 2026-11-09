"""Acceptance: event-only orphans, allocator, concurrency, restart and export."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.export_contract import export_contract
from ocpp_csms.schema import DATABASE_FILENAME


def _start(index):
    return {
        "connector_id": 1, "id_tag": f"CARD-{index}", "meter_start": index,
        "timestamp": f"2026-10-03T10:{index:02d}:00Z",
    }


def _query(path, sql):
    with sqlite3.connect(path / DATABASE_FILENAME) as connection:
        return connection.execute(sql).fetchall()


def test_parallel_starts_allocate_distinct_monotonic_ids(tmp_path):
    EventStore(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda i: EventStore(tmp_path).commit_start("charger-a", _start(i)), range(8)))
    assert sorted(ids) == list(range(1, 9))
    assert _query(tmp_path, "SELECT last_id FROM transaction_allocator") == [(8,)]
    assert len(_query(tmp_path, "SELECT transaction_id FROM transactions")) == 8


def test_restart_orphan_retry_and_export_cursor(tmp_path):
    store = EventStore(tmp_path)
    payload = {"connector_id": 1, "transaction_id": 9999, "meter_value": []}
    store.commit_transaction_evidence("charger-a", "MeterValues", payload)
    EventStore(tmp_path).commit_transaction_evidence("charger-a", "MeterValues", payload)
    store.commit_transaction_evidence("charger-a", "StopTransaction", {
        "transaction_id": 9999, "meter_stop": 25, "timestamp": "2026-10-03T10:00:00Z",
    })
    assert _query(tmp_path, "SELECT transaction_id FROM transactions") == []
    assert _query(tmp_path, "SELECT last_id FROM transaction_allocator") == [(0,)]
    first = export_contract(tmp_path, after=0, limit=2)["data"]
    assert [item["action"] for item in first["events"]] == ["MeterValues", "MeterValues"]
    all_events = export_contract(tmp_path, after=0, limit=10)["data"]["events"]
    assert [item["action"] for item in all_events] == ["MeterValues", "MeterValues", "StopTransaction"]
    assert EventStore(tmp_path).commit_start("charger-a", _start(0)) == 1


def test_sqlite_rejection_preserves_allocator_and_events(tmp_path):
    store = EventStore(tmp_path)
    before = _query(tmp_path, "SELECT last_id FROM transaction_allocator")
    with sqlite3.connect(tmp_path / DATABASE_FILENAME) as connection:
        connection.execute("""CREATE TRIGGER reject_start_acceptance BEFORE INSERT ON transactions
            BEGIN SELECT RAISE(ABORT, 'blocked'); END""")
    with pytest.raises(sqlite3.DatabaseError):
        store.commit_start("charger-a", _start(0))
    assert _query(tmp_path, "SELECT last_id FROM transaction_allocator") == before
    assert _query(tmp_path, "SELECT transaction_id FROM transactions") == []
