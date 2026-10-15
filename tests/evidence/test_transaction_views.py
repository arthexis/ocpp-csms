"""Chunk 3A: read-only SQLite transaction boundary projections."""
import sqlite3

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.schema import DATABASE_FILENAME, CURRENT_SCHEMA_VERSION, inspect_schema


def start(store, charger, connector, timestamp, meter=100):
    return store.commit_start(charger, {
        "connector_id": connector, "id_tag": "card-a",
        "meter_start": meter, "timestamp": timestamp,
    })


def boundaries(tmp_path):
    with sqlite3.connect(tmp_path / DATABASE_FILENAME) as db:
        return db.execute(
            "SELECT transaction_id, next_start_received_at, observed_stop_at "
            "FROM transaction_boundaries ORDER BY transaction_id"
        ).fetchall()


def test_next_start_is_read_only_and_scoped_by_charge_point_and_connector(tmp_path):
    store = EventStore(tmp_path)
    first = start(store, "cp-a", 1, "2026-10-03T10:00:00Z")
    other_connector = start(store, "cp-a", 2, "2026-10-03T10:01:00Z")
    other_charger = start(store, "cp-b", 1, "2026-10-03T10:02:00Z")
    second = start(store, "cp-a", 1, "2026-10-03T10:03:00Z", 200)
    with sqlite3.connect(tmp_path / DATABASE_FILENAME) as db:
        receipt = db.execute(
            "SELECT start_received_at FROM transactions WHERE transaction_id = ?", (second,)
        ).fetchone()[0]
        before = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    assert boundaries(tmp_path) == [
        (first, receipt, None),
        (other_connector, None, None),
        (other_charger, None, None),
        (second, None, None),
    ]
    with sqlite3.connect(tmp_path / DATABASE_FILENAME) as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == before
        assert db.execute("SELECT COUNT(*) FROM transactions WHERE stopped_at IS NOT NULL").fetchone()[0] == 0


def test_exact_retry_is_not_a_new_boundary_and_real_stop_wins(tmp_path):
    store = EventStore(tmp_path)
    payload = {"connector_id": 1, "id_tag": "card-a", "meter_start": 100,
               "timestamp": "2026-10-03T10:00:00Z"}
    first = store.commit_start("cp-a", payload)
    assert store.commit_start("cp-a", payload) == first
    assert boundaries(tmp_path) == [(first, None, None)]
    second = start(store, "cp-a", 1, "2026-10-03T10:05:00Z", 200)
    store.commit_transaction_evidence("cp-a", "StopTransaction", {
        "transaction_id": first, "meter_stop": 150,
        "timestamp": "2026-10-03T10:06:00Z",
    })
    with sqlite3.connect(tmp_path / DATABASE_FILENAME) as db:
        stop_receipt = db.execute(
            "SELECT stop_received_at FROM transactions WHERE transaction_id = ?", (first,)
        ).fetchone()[0]
    assert boundaries(tmp_path) == [
        (first, boundaries(tmp_path)[0][1], stop_receipt),
        (second, None, None),
    ]


def test_summary_and_index_available_on_current_schema(tmp_path):
    EventStore(tmp_path)
    assert inspect_schema(tmp_path).version == CURRENT_SCHEMA_VERSION
    with sqlite3.connect(tmp_path / DATABASE_FILENAME) as db:
        views = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'view'")}
        indexes = {row[1] for row in db.execute("PRAGMA index_list(transactions)")}
        columns = {row[1] for row in db.execute("PRAGMA table_info(transaction_summary)")}
    assert {"transaction_boundaries", "transaction_summary"} <= views
    assert "idx_transactions_boundary" in indexes
    assert {"observed_stop_at", "next_start_received_at"} <= columns
