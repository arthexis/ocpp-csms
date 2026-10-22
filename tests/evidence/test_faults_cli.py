"""Regression coverage for concise field fault diagnostics."""
from ocpp_csms.evidence.diagnostics import fault_events, format_events, format_faults, events_between
from ocpp_csms.evidence.store import EventStore


def test_faults_include_vendor_evidence_and_both_times(tmp_path):
    store = EventStore(tmp_path)
    store.record_ocpp("abb", "Heartbeat", {})
    store.record_ocpp("abb", "StatusNotification", {
        "connector_id": 1, "status": "Faulted", "error_code": "OtherError",
        "timestamp": "2026-10-18T18:43:50Z",
        "vendor_id": "com.example", "vendor_error_code": "E42",
        "info": "Service required",
    })
    faults = fault_events(tmp_path)
    assert len(faults) == 1
    assert faults[0]["charger_at"] == "2026-10-18T18:43:50Z"
    assert faults[0]["vendor_error_code"] == "E42"
    assert faults[0]["received_at"] != faults[0]["charger_at"]
    assert "E42" in format_faults(faults)
    timeline = format_events(events_between(tmp_path), detail=True)
    assert "vendor_error_code=E42" in timeline
    assert "vendor_id=com.example" in timeline


def test_faults_filter_charger_and_limit_after_filter(tmp_path):
    store = EventStore(tmp_path)
    store.record_ocpp("a", "StatusNotification", {"connector_id": 1, "status": "Faulted", "error_code": "OtherError"})
    store.record_ocpp("b", "StatusNotification", {"connector_id": 1, "status": "Faulted", "error_code": "OtherError"})
    store.record_ocpp("a", "StatusNotification", {"connector_id": 1, "status": "Available", "error_code": "NoError"})
    store.record_ocpp("a", "StatusNotification", {"connector_id": 1, "status": "Faulted", "error_code": "OtherError"})
    assert len(fault_events(tmp_path, limit=1)) == 1
    assert len(fault_events(tmp_path, charger_id="a")) == 2
    assert len(fault_events(tmp_path, charger_id="b")) == 1
    assert fault_events(tmp_path, charger_id="unknown") == []


def test_faults_ignore_malformed_json_and_nonfaults(tmp_path):
    store = EventStore(tmp_path)
    store.record_ocpp("a", "StatusNotification", {"connector_id": 1, "status": "Available", "error_code": "NoError"})
    store.record_ocpp("a", "Heartbeat", {})
    assert fault_events(tmp_path) == []
    assert format_faults([]) == "No recorded fault notifications."
