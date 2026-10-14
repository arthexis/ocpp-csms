"""Query selection must not read unrelated OCPP evidence."""
from datetime import datetime, timezone
import sqlite3

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.schema import database_path
from ocpp_csms.transactions.query import TransactionQuery


def test_get_and_filtered_list_only_fetch_selected_evidence(tmp_path):
    store = EventStore(tmp_path)
    first = store.commit_start("cp-a", {
        "connector_id": 1, "id_tag": "A", "meter_start": 10,
        "timestamp": "2026-10-01T00:00:00Z",
    })
    second = store.commit_start("cp-b", {
        "connector_id": 2, "id_tag": "B", "meter_start": 20,
        "timestamp": "2026-10-02T00:00:00Z",
    })
    connection = sqlite3.connect(database_path(tmp_path))
    statements = []
    connection.set_trace_callback(statements.append)
    try:
        query = TransactionQuery(tmp_path, connection=connection)
        assert query.get(first).transaction_id == first
        assert all("transaction_id = " in sql for sql in statements if "FROM events" in sql)
        statements.clear()
        assert [view.transaction_id for view in query.list(charger="cp-b", connector=2, id_tag="B")] == [second]
        assert any("charger_id = " in sql for sql in statements if "FROM transaction_summary" in sql)
        assert all("transaction_id = " in sql for sql in statements if "FROM events" in sql)
        assert connection.execute("SELECT 1").fetchone()[0] == 1
    finally:
        connection.close()


def test_default_order_remains_charger_event_time_not_receipt_time(tmp_path):
    store = EventStore(tmp_path)
    first = store.commit_start("cp-a", {
        "connector_id": 1, "id_tag": "A", "meter_start": 10,
        "timestamp": "2026-10-10T23:00:00Z",
    })
    second = store.commit_start("cp-b", {
        "connector_id": 1, "id_tag": "B", "meter_start": 10,
        "timestamp": "2026-10-01T00:00:00Z",
    })
    query = TransactionQuery(tmp_path)
    assert query.last().transaction_id == first
    assert {view.transaction_id for view in query.list(limit=None)} == {first, second}
    assert query.get(999999) is None


def test_large_unlimited_list_batches_evidence_without_dropping_transactions(tmp_path):
    store = EventStore(tmp_path)
    ids = [
        store.commit_start("cp-a", {
            "connector_id": 1, "id_tag": f"CARD-{i}", "meter_start": i,
            "timestamp": "2026-10-01T00:00:00Z",
        })
        for i in range(405)
    ]
    views = TransactionQuery(tmp_path).list(limit=None)
    assert {view.transaction_id for view in views} == set(ids)
    assert all(view.record["start"]["id_tag"] == view.id_tag for view in views)


def test_targeted_evidence_query_plan_uses_transaction_index(tmp_path):
    store = EventStore(tmp_path)
    txn_id = store.commit_start("cp-a", {
        "connector_id": 1, "id_tag": "A", "meter_start": 10,
        "timestamp": "2026-10-01T00:00:00Z",
    })
    with sqlite3.connect(database_path(tmp_path)) as connection:
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT id, transaction_id, charger_id, action, "
            "payload_json, received_at FROM events WHERE direction = 'in' "
            "AND action IN ('StartTransaction', 'MeterValues', 'StopTransaction') "
            "AND (transaction_id = ? AND charger_id = ?) ORDER BY id",
            (txn_id, "cp-a"),
        ).fetchall()
    assert any("idx_events_transaction" in str(row) for row in plan)


def test_query_owns_consistent_snapshot_across_summary_and_evidence(tmp_path, monkeypatch):
    store = EventStore(tmp_path)
    txn_id = store.commit_start("cp-a", {
        "connector_id": 1, "id_tag": "A", "meter_start": 10,
        "timestamp": "2026-10-01T00:00:00Z",
    })
    # A supplied connection with an explicit snapshot remains owned by the caller.
    with sqlite3.connect(database_path(tmp_path)) as connection:
        connection.execute("BEGIN")
        query = TransactionQuery(tmp_path, connection=connection)
        assert query.get(txn_id).transaction_id == txn_id
        assert connection.in_transaction
        assert query.list(charger="cp-a")[0].transaction_id == txn_id
        assert connection.in_transaction


def test_standalone_query_reads_summary_and_evidence_from_same_wal_snapshot(tmp_path, monkeypatch):
    """A writer committing between SELECTs must not leak into the read snapshot."""
    store = EventStore(tmp_path)
    txn_id = store.commit_start("cp-a", {
        "connector_id": 1, "id_tag": "A", "meter_start": 10,
        "timestamp": "2026-10-01T00:00:00Z",
    })
    original_connect = sqlite3.connect
    class InterleavedConnection:
        def __init__(self, underlying):
            self.underlying = underlying
            self.injected = False

        def execute(self, sql, params=()):
            if "FROM events WHERE direction" in sql and not self.injected:
                self.injected = True
                with original_connect(database_path(tmp_path)) as writer:
                    writer.execute(
                        """INSERT INTO events
                           (received_at, charger_id, action, direction,
                            transaction_id, payload_json)
                           VALUES (?, ?, 'MeterValues', 'in', ?, ?)""",
                        ("2026-10-14T00:00:00Z", "cp-a", txn_id,
                         '{"meter_value":[{"timestamp":"2026-10-14T00:00:00Z"}]}'),
                    )
            return self.underlying.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self.underlying, name)

    def wrapped_connect(*args, **kwargs):
        return InterleavedConnection(original_connect(*args, **kwargs))

    monkeypatch.setattr("ocpp_csms.transactions.query.sqlite3.connect", wrapped_connect)
    view = TransactionQuery(tmp_path).get(txn_id)
    assert view is not None
    assert view.record["meter_values"] == []
    # The writer really committed, and a subsequent query sees the new event.
    assert len(TransactionQuery(tmp_path).get(txn_id).record["meter_values"]) == 1
