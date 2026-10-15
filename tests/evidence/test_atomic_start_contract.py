"""SQLite-first StartTransaction contract (Chunk 1)."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from ocpp_csms.evidence.store import EventStore


def start(timestamp="2026-10-01T12:00:00Z", connector=1):
    return {"connector_id": connector, "id_tag": "RFID", "meter_start": 100, "timestamp": timestamp}


def rows(store, table):
    with sqlite3.connect(store.path) as db:
        return db.execute(f"SELECT * FROM {table}").fetchall()


def test_exact_retry_is_durable_and_each_receipt_is_an_event(tmp_path):
    store = EventStore(tmp_path)
    assert store.commit_start("cp-a", start()) == 1
    assert EventStore(tmp_path).commit_start("cp-a", start()) == 1
    assert len(rows(store, "transactions")) == 1
    assert len(rows(store, "events")) == 2


def test_distinct_starts_and_charge_points_get_distinct_ids(tmp_path):
    store = EventStore(tmp_path)
    assert store.commit_start("cp-a", start()) == 1
    assert store.commit_start("cp-a", start("2026-10-01T12:00:01Z")) == 2
    assert store.commit_start("cp-b", start()) == 3


def test_independent_concurrent_connections_serialize(tmp_path):
    EventStore(tmp_path)
    def submit(i):
        return EventStore(tmp_path).commit_start("cp-a", start("2026-10-01T12:00:%02dZ" % i))
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(submit, range(20)))
    assert sorted(ids) == list(range(1, 21))


def test_exact_concurrent_retries_share_identity_not_event(tmp_path):
    EventStore(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda _: EventStore(tmp_path).commit_start("cp-a", start()), range(8)))
    assert ids == [1] * 8
    assert len(rows(EventStore(tmp_path), "events")) == 8


def test_atomic_rollback_on_event_insertion_failure(tmp_path):
    store = EventStore(tmp_path)
    with sqlite3.connect(store.path) as db:
        db.execute("""CREATE TRIGGER reject_start BEFORE INSERT ON events
                      WHEN NEW.action = 'StartTransaction'
                      BEGIN SELECT RAISE(ABORT, 'reject event'); END""")
    with pytest.raises(sqlite3.DatabaseError):
        store.commit_start("cp-a", start())
    assert rows(store, "transactions") == []
    assert rows(store, "start_identities") == []
    assert len(rows(store, "events")) == 0
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT last_id FROM transaction_allocator").fetchone()[0] == 0


def test_allocator_exhaustion_is_explicit_and_atomic(tmp_path):
    store = EventStore(tmp_path)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE transaction_allocator SET last_id = 2147483647")
    with pytest.raises(OverflowError):
        store.commit_start("cp-a", start())
    assert rows(store, "transactions") == []
    assert rows(store, "events") == []


def test_full_payload_difference_is_not_deduplicated(tmp_path):
    store = EventStore(tmp_path)
    first = start()
    second = {**first, "reservation_id": 19}
    assert store.commit_start("cp-a", first) != store.commit_start("cp-a", second)
