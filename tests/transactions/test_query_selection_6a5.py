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
