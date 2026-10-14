"""Transaction state is based on observed StopTransaction evidence only."""
from ocpp_csms.evidence.store import EventStore
from ocpp_csms.transactions.query import TransactionQuery


def test_subsequent_start_does_not_close_earlier_transaction(tmp_path):
    store = EventStore(tmp_path)
    first = store.commit_start("SIM001", {
        "connector_id": 1, "id_tag": "CARD", "meter_start": 100,
        "timestamp": "2026-10-03T10:00:00Z",
    })
    store.commit_start("SIM001", {
        "connector_id": 1, "id_tag": "CARD", "meter_start": 200,
        "timestamp": "2026-10-03T10:05:00Z",
    })
    view = TransactionQuery(tmp_path).get(first)
    assert view is not None
    assert view.status == "open"
    assert view.active
    assert view.record["stop"] is None
    assert view.record["next_start_received_at"] is not None


def test_actual_stop_closes_transaction(tmp_path):
    store = EventStore(tmp_path)
    transaction_id = store.commit_start("SIM001", {
        "connector_id": 1, "id_tag": "CARD", "meter_start": 100,
        "timestamp": "2026-10-03T10:00:00Z",
    })
    store.commit_transaction_evidence("SIM001", "StopTransaction", {
        "transaction_id": transaction_id, "meter_stop": 150,
        "timestamp": "2026-10-03T10:10:00Z",
    })
    view = TransactionQuery(tmp_path).get(transaction_id)
    assert view is not None
    assert view.status == "stopped"
    assert not view.active
