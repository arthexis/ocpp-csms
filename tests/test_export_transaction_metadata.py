from __future__ import annotations

from datetime import datetime

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.export_contract import export_contract
from ocpp_csms.transactions.export_metadata import transaction_export_metadata


def test_timestamp_provenance_and_quality_are_nullable_and_utc():
    record = {
        "status": "stopped", "origin": "local",
        "start": {"timestamp": "2026-01-01T01:00:00+01:00", "meter_start": 100},
        "stop": {"timestamp": "invalid", "meter_stop": 300},
        "start_received_at": "2026-01-01T00:01:00Z",
        "stop_received_at": "2026-01-01T00:20:00Z",
        "meter_values": [{"meter_value": [{"timestamp": "2026-01-01T00:10:00Z"}]}],
        "meter_values_received_at": ["2026-01-01T00:11:00Z"],
    }
    result = transaction_export_metadata(record)
    assert result["start_event_at"] == "2026-01-01T00:00:00Z"
    assert result["stop_event_at"] is None
    assert result["start_received_at"] == "2026-01-01T00:01:00Z"
    assert result["meter_sample_times"][0]["received_at"] == "2026-01-01T00:11:00Z"
    assert result["quality"]["missing_meter"] is False
    assert result["quality"]["incomplete"] is False



def test_export_transaction_metadata_follows_event_cursor(tmp_path):
    store = EventStore(tmp_path)
    payload = {"connector_id": 1, "id_tag": "CARD-A", "timestamp": "2026-01-01T01:00:00+01:00", "meter_start": 1}
    transaction_id = store.commit_start("cp-a", payload)
    store.record_ocpp("cp-a", "StartTransaction", {"transaction_id": transaction_id}, transaction_id=transaction_id)
    first = export_contract(tmp_path)["data"]
    assert first["transactions"][0]["start_event_at"] == "2026-01-01T00:00:00Z"
    assert first["transactions"][0]["quality"]["active"] is True
    cursor = first["cursor"]["next"]
    store.commit_transaction_evidence("cp-a", "StopTransaction", {"transaction_id": transaction_id, "meter_stop": 3, "timestamp": "2026-01-01T00:10:00Z"})
    store.record_ocpp("cp-a", "StopTransaction", {"transaction_id": transaction_id}, transaction_id=transaction_id)
    second = export_contract(tmp_path, after=cursor)["data"]
    assert len(second["transactions"]) == 1
    assert second["transactions"][0]["quality"]["active"] is False


def test_export_prefers_charger_event_time_over_delayed_receive_time(tmp_path):
    """Historical messages received together must retain real session duration."""
    store = EventStore(tmp_path)
    start_at = "2026-10-08T18:59:18.928Z"
    stop_at = "2026-10-08T19:44:10.086Z"
    transaction_id = store.commit_start("charger-a", {
        "connector_id": 1, "id_tag": "NOA", "meter_start": 106014173,
        "timestamp": start_at,
    })
    store.commit_transaction_evidence("charger-a", "StopTransaction", {
        "transaction_id": transaction_id, "meter_stop": 106048235,
        "timestamp": stop_at,
    })
    store.record_ocpp("charger-a", "StartTransaction", {
        "connector_id": 1, "id_tag": "NOA", "meter_start": 106014173,
        "timestamp": start_at,
    }, transaction_id=transaction_id)
    store.record_ocpp("charger-a", "StopTransaction", {
        "transaction_id": transaction_id, "meter_stop": 106048235,
        "timestamp": stop_at,
    }, transaction_id=transaction_id)

    [transaction] = export_contract(tmp_path)["data"]["transactions"]
    def instant(value):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    assert instant(transaction["started_at"]) == instant(start_at)
    assert instant(transaction["stopped_at"]) == instant(stop_at)
    assert instant(transaction["start_event_at"]) == instant(start_at)
    assert instant(transaction["stop_event_at"]) == instant(stop_at)
    assert transaction["energy_wh"] == 34062
    assert transaction["start_received_at"] is not None
    assert transaction["stop_received_at"] is not None
    assert transaction["start_received_at"] != start_at
    assert transaction["stop_received_at"] != stop_at


def test_export_invalid_event_date_does_not_fall_back_to_receive_time(tmp_path):
    """Missing/invalid device time is unknown, not the replay or receipt time."""
    store = EventStore(tmp_path)
    transaction_id = store.commit_start("charger-a", {
        "connector_id": 1, "id_tag": "CARD-A", "meter_start": 100,
        "timestamp": "not-a-date",
    })
    store.record_ocpp("charger-a", "StartTransaction", {
        "connector_id": 1, "id_tag": "CARD-A", "meter_start": 100,
        "timestamp": "not-a-date",
    }, transaction_id=transaction_id)
    [transaction] = export_contract(tmp_path)["data"]["transactions"]
    assert transaction["started_at"] is None
    assert transaction["start_event_at"] is None
    assert transaction["start_received_at"] is not None
