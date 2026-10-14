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
