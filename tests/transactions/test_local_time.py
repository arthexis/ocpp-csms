import json
from datetime import datetime, timezone

import pytest

import ocpp_csms.transactions.archive as transactions_module
from ocpp_csms.transactions.query import TransactionQuery
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
    transaction_id = await archive.start("charger-a", {"connector_id": 1, "timestamp": "2020-01-01T00:00:00Z"})
    await archive.meter_values("charger-a", {"transaction_id": transaction_id, "connector_id": 1, "meter_value": [{"timestamp": "2020-01-01T00:05:00Z"}]})
    await archive.stop("charger-a", {"transaction_id": transaction_id, "timestamp": "2020-01-01T00:10:00Z"})

    [path] = (tmp_path / "transactions").glob("*/*.json")
    record = json.loads(path.read_text())
    assert record["start_received_at"] == "2026-10-06T12:00:01Z"
    assert record["meter_values_received_at"] == ["2026-10-06T12:05:01Z"]
    assert record["stop_received_at"] == "2026-10-06T12:10:01Z"


@pytest.mark.asyncio
async def test_legacy_meter_values_keep_receive_time_alignment(tmp_path, monkeypatch):
    directory = tmp_path / "transactions" / "2026-10-06"
    directory.mkdir(parents=True)
    path = directory / "charger-a-1.json"
    path.write_text(json.dumps({
        "transaction_id": 1,
        "origin": "local",
        "charge_point_id": "charger-a",
        "status": "open",
        "created_at": "2026-10-06T10:00:00Z",
        "updated_at": "2026-10-06T10:05:00Z",
        "start": {"connector_id": 1, "timestamp": "2026-10-06T10:00:00Z"},
        "meter_values": [{"transaction_id": 1, "connector_id": 1, "meter_value": [{"timestamp": "2026-10-06T10:05:00Z"}]}],
        "stop": None,
    }))
    monkeypatch.setattr(transactions_module, "utc_now_iso", lambda: "2026-10-06T10:10:00Z")

    archive = TransactionArchive(tmp_path)
    await archive.meter_values("charger-a", {"transaction_id": 1, "connector_id": 1, "meter_value": [{"timestamp": "2026-10-06T10:10:00Z"}]})

    record = json.loads(path.read_text())
    assert record["meter_values_received_at"] == [None, "2026-10-06T10:10:00Z"]


def _write_record(tmp_path, transaction_id, charger_time, received_time):
    directory = tmp_path / "transactions" / "2026-10-06"
    directory.mkdir(parents=True, exist_ok=True)
    record = {
        "transaction_id": transaction_id,
        "origin": "local",
        "charge_point_id": "charger-a",
        "status": "stopped",
        "created_at": received_time,
        "updated_at": received_time,
        "start": {"connector_id": 1, "timestamp": charger_time},
        "start_received_at": received_time,
        "meter_values": [],
        "meter_values_received_at": [],
        "stop": {"transaction_id": transaction_id, "timestamp": charger_time},
        "stop_received_at": received_time,
    }
    (directory / f"charger-a-{transaction_id}.json").write_text(json.dumps(record))


def test_local_time_switches_filtering_and_ordering_to_receive_time(tmp_path):
    _write_record(tmp_path, 1, "2030-01-01T00:00:00Z", "2026-10-06T10:00:00Z")
    _write_record(tmp_path, 2, "2020-01-01T00:00:00Z", "2026-10-06T11:00:00Z")
    query = TransactionQuery(tmp_path)

    assert [view.transaction_id for view in query.list()] == [1, 2]
    assert [view.transaction_id for view in query.list(local_time=True)] == [2, 1]
    assert [view.transaction_id for view in query.list(local_time=True, since=datetime(2026, 10, 6, 10, 30, tzinfo=timezone.utc))] == [2]


def test_local_time_legacy_archive_falls_back_to_archive_update_time(tmp_path):
    directory = tmp_path / "transactions" / "2026-10-06"
    directory.mkdir(parents=True)
    record = {"transaction_id": 1, "charge_point_id": "charger-a", "status": "stopped", "created_at": "2026-10-06T10:00:00Z", "updated_at": "2026-10-06T11:00:00Z", "start": {"timestamp": "2020-01-01T00:00:00Z"}, "meter_values": [], "stop": {"timestamp": "2020-01-01T01:00:00Z"}}
    (directory / "legacy.json").write_text(json.dumps(record))

    view = TransactionQuery(tmp_path).get(1)
    assert view.received_activity_at == datetime(2026, 10, 6, 11, 0, tzinfo=timezone.utc)


def test_partial_upgrade_keeps_newer_archive_update_as_receive_activity(tmp_path):
    directory = tmp_path / "transactions" / "2026-10-06"
    directory.mkdir(parents=True)
    record = {
        "transaction_id": 1,
        "charge_point_id": "charger-a",
        "status": "open",
        "created_at": "2026-10-06T10:00:00Z",
        "updated_at": "2026-10-06T11:00:00Z",
        "start": {"timestamp": "2020-01-01T00:00:00Z"},
        "start_received_at": "2026-10-06T10:00:00Z",
        "meter_values": [],
        "meter_values_received_at": [],
        "stop": None,
    }
    (directory / "partial.json").write_text(json.dumps(record))

    view = TransactionQuery(tmp_path).get(1)
    assert view.received_activity_at == datetime(2026, 10, 6, 11, 0, tzinfo=timezone.utc)
