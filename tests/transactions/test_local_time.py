import json
from datetime import datetime, timezone

import pytest

import ocpp_csms.transactions.archive as transactions_module
from ocpp_csms.transactions.query import TransactionQuery
from ocpp_csms.evidence.store import EventStore
from ocpp_csms.transactions.archive import TransactionArchive


@pytest.mark.asyncio
async def test_archive_persists_receive_time_for_each_event(tmp_path, monkeypatch):
    times = iter((
        "2026-10-06T12:00:01Z",
        "2026-10-06T12:05:01Z",
        "2026-10-06T12:10:01Z",
    ))
    monkeypatch.setattr(transactions_module, "utc_now_iso", lambda: next(times))
    archive = TransactionArchive(tmp_path)
    payload = {"connector_id": 1, "id_tag": "CARD", "meter_start": 100, "timestamp": "2020-01-01T00:00:00Z"}
    transaction_id = EventStore(tmp_path).commit_start("charger-a", payload)
    await archive.diagnostic_start("charger-a", payload, transaction_id)
    await archive.meter_values("charger-a", {"transaction_id": transaction_id, "connector_id": 1, "meter_value": [{"timestamp": "2020-01-01T00:05:00Z"}]})
    await archive.stop("charger-a", {"transaction_id": transaction_id, "timestamp": "2020-01-01T00:10:00Z"})

    [path] = (tmp_path / "transactions").glob("*/*.json")
    record = json.loads(path.read_text())
    assert record["start_received_at"] == "2026-10-06T12:00:01Z"
    assert record["meter_values_received_at"] == ["2026-10-06T12:05:01Z"]
    assert record["stop_received_at"] == "2026-10-06T12:10:01Z"



def test_json_diagnostics_do_not_create_query_transactions(tmp_path):
    directory = tmp_path / "transactions" / "2026-10-06"
    directory.mkdir(parents=True)
    (directory / "diagnostic.json").write_text(json.dumps({
        "transaction_id": 1, "charge_point_id": "charger-a",
        "status": "stopped", "updated_at": "2026-10-06T11:00:00Z",
    }))
    assert TransactionQuery(tmp_path).get(1) is None


def test_sqlite_receive_time_controls_query_order(tmp_path, monkeypatch):
    import ocpp_csms.evidence.store as store_module
    store = EventStore(tmp_path)
    monkeypatch.setattr(store_module, "utc_now_iso", lambda: "2026-10-06T10:00:00Z")
    first = store.commit_start("charger-a", {
        "connector_id": 1, "id_tag": "CARD", "meter_start": 100,
        "timestamp": "2030-01-01T00:00:00Z",
    })
    monkeypatch.setattr(store_module, "utc_now_iso", lambda: "2026-10-06T11:00:00Z")
    second = store.commit_start("charger-a", {
        "connector_id": 1, "id_tag": "CARD", "meter_start": 100,
        "timestamp": "2020-01-01T00:00:00Z",
    })
    query = TransactionQuery(tmp_path)
    assert [v.transaction_id for v in query.list()] == [first, second]
    assert [v.transaction_id for v in query.list(local_time=True)] == [second, first]
    assert [v.transaction_id for v in query.list(
        local_time=True, since=datetime(2026, 10, 6, 10, 30, tzinfo=timezone.utc)
    )] == [second]
