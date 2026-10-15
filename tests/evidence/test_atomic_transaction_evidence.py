"""Chunk 2: SQLite-authoritative, additive transaction evidence."""
import json
import sqlite3
from types import SimpleNamespace

import pytest

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.session.handlers import SessionHandlers


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


def test_unknown_meter_and_stop_recover_incomplete_transaction(tmp_path):
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
    assert transactions == [(999, "cp-a", 150, "2026-10-03T10:10:00Z")]
    with store._connect() as db:
        assert db.execute("SELECT state, started_at FROM transactions WHERE transaction_id = 999").fetchone() == ("stopped", None)


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
    assert transactions == [(404, "cp-a", None, None)]


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


@pytest.mark.asyncio
@pytest.mark.parametrize("action,handler,payload", [
    ("MeterValues", "on_meter_values", {"transaction_id": 999, "connector_id": 1, "meter_value": []}),
    ("StopTransaction", "on_stop_transaction", {"transaction_id": 999, "meter_stop": 150, "timestamp": "2026-10-03T10:10:00Z"}),
])
async def test_handler_acknowledges_only_after_sqlite_commit(tmp_path, action, handler, payload):
    store = EventStore(tmp_path)
    session = SimpleNamespace(id="cp-a", events=store)
    result = await getattr(SessionHandlers, handler)(session, **payload)
    assert result is not None
    events, transactions = rows(store)
    assert [event[1] for event in events] == [action]
    expected_stop = payload.get("meter_stop") if action == "StopTransaction" else None
    expected_timestamp = payload.get("timestamp") if action == "StopTransaction" else None
    assert transactions == [(999, "cp-a", expected_stop, expected_timestamp)]


@pytest.mark.asyncio
@pytest.mark.parametrize("action,handler,payload", [
    ("MeterValues", "on_meter_values", {"transaction_id": 999, "connector_id": 1, "meter_value": []}),
    ("StopTransaction", "on_stop_transaction", {"transaction_id": 999, "meter_stop": 150, "timestamp": "2026-10-03T10:10:00Z"}),
])
async def test_handler_does_not_acknowledge_failed_sqlite_commit(tmp_path, action, handler, payload):
    store = EventStore(tmp_path)
    with sqlite3.connect(store.path) as db:
        db.execute("""CREATE TRIGGER reject_evidence BEFORE INSERT ON events
                      WHEN NEW.action = '%s'
                      BEGIN SELECT RAISE(ABORT, 'reject evidence'); END""" % action)
    session = SimpleNamespace(id="cp-a", events=store)
    with pytest.raises(sqlite3.DatabaseError):
        await getattr(SessionHandlers, handler)(session, **payload)
    assert rows(store) == ([], [])


def test_stop_receipt_retries_do_not_change_first_stop_timestamp(tmp_path):
    store = EventStore(tmp_path)
    transaction_id = store.commit_start("cp-a", START)
    stop = {"transaction_id": transaction_id, "meter_stop": 150, "timestamp": "2026-10-03T10:10:00Z"}
    store.commit_transaction_evidence("cp-a", "StopTransaction", stop)
    with store._connect() as connection:
        first_receipt = connection.execute(
            "SELECT stop_received_at FROM transactions WHERE transaction_id = ?", (transaction_id,)
        ).fetchone()[0]
    store.commit_transaction_evidence("cp-a", "StopTransaction", stop)
    with store._connect() as connection:
        second_receipt = connection.execute(
            "SELECT stop_received_at FROM transactions WHERE transaction_id = ?", (transaction_id,)
        ).fetchone()[0]
    assert second_receipt == first_receipt
    events, _ = rows(store)
    assert [event[1] for event in events].count("StopTransaction") == 2
