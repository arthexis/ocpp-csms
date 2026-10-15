from __future__ import annotations

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
