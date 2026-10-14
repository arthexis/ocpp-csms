"""Chunk 3B.1: SQLite is the transaction query source."""
import sqlite3

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.schema import database_path
from ocpp_csms.transactions.query import TransactionQuery


def test_query_uses_sqlite_without_archives_and_survives_restart(tmp_path):
    store = EventStore(tmp_path)
    first = store.commit_start("cp-a", {
        "connector_id": 1, "id_tag": "alpha", "meter_start": 100,
        "timestamp": "2026-10-03T10:00:00Z",
    })
    second = store.commit_start("cp-b", {
        "connector_id": 2, "id_tag": "beta", "meter_start": 200,
        "timestamp": "2026-10-03T11:00:00Z",
    })
    query = TransactionQuery(tmp_path)
    assert {v.transaction_id for v in query.list()} == {first, second}
    assert query.get(first).charge_point_id == "cp-a"
    assert query.last(charger="cp-b", connector=2).transaction_id == second
    assert query.list(id_tag="alpha")[0].transaction_id == first
    assert {v.transaction_id for v in TransactionQuery(tmp_path).list()} == {first, second}
    assert not (tmp_path / "transactions").exists()


def test_sqlite_query_does_not_write_or_reconstruct_from_json(tmp_path):
    store = EventStore(tmp_path)
    transaction_id = store.commit_start("cp-a", {
        "connector_id": 1, "id_tag": "alpha", "meter_start": 100,
        "timestamp": "2026-10-03T10:00:00Z",
    })
    directory = tmp_path / "transactions" / "2026-10-03"
    directory.mkdir(parents=True)
    (directory / "999.json").write_text('{"transaction_id":999,"status":"open"}')
    path = database_path(tmp_path)
    with sqlite3.connect(path) as db:
        before = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    query = TransactionQuery(tmp_path)
    assert query.get(999) is None
    assert query.get(transaction_id) is not None
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == before


def test_query_does_not_attach_other_charge_point_evidence(tmp_path):
    store = EventStore(tmp_path)
    transaction_id = store.commit_start("cp-a", {
        "connector_id": 1, "id_tag": "alpha", "meter_start": 100,
        "timestamp": "2026-10-03T10:00:00Z",
    })
    store.commit_transaction_evidence("cp-b", "MeterValues", {
        "transaction_id": transaction_id, "connector_id": 9,
        "meter_value": [{"timestamp": "2026-10-03T10:01:00Z"}],
    })
    store.commit_transaction_evidence("cp-b", "StopTransaction", {
        "transaction_id": transaction_id, "meter_stop": 900,
        "timestamp": "2026-10-03T10:02:00Z",
    })
    view = TransactionQuery(tmp_path).get(transaction_id)
    assert view.charge_point_id == "cp-a"
    assert view.connector_id == 1
    assert view.record["meter_values"] == []
    assert view.record["stop"] is None
    assert view.status == "open"


def test_query_committed_stop_overrides_later_retry(tmp_path):
    store = EventStore(tmp_path)
    transaction_id = store.commit_start("cp-a", {
        "connector_id": 1, "id_tag": "alpha", "meter_start": 100,
        "timestamp": "2026-10-03T10:00:00Z",
    })
    store.commit_transaction_evidence("cp-a", "StopTransaction", {
        "transaction_id": transaction_id, "meter_stop": 200,
        "timestamp": "2026-10-03T10:10:00Z",
    })
    store.commit_transaction_evidence("cp-a", "StopTransaction", {
        "transaction_id": transaction_id, "meter_stop": 999,
        "timestamp": "2026-10-03T10:20:00Z",
    })
    view = TransactionQuery(tmp_path).get(transaction_id)
    assert view.record["stop"]["meter_stop"] == 200
    assert view.status == "stopped"
    assert view.record["stop_received_at"] is not None
    assert view.next_start_received_at is None
