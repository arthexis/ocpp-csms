"""GetConfiguration history must be append-only, bounded and never expose secrets."""
from __future__ import annotations

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.export_contract import export_contract
from ocpp_csms.config_history import export_configuration_history


def test_normal_export_adds_only_sanitized_configuration_snapshots(tmp_path):
    store = EventStore(tmp_path)
    store.record_ocpp("cp-a", "GetConfiguration", {}, direction="out")
    store.record_ocpp("cp-a", "GetConfiguration", {
        "configuration_key": [
            {"key": "HeartbeatInterval", "readonly": False, "value": "300"},
            {"key": "AuthorizationKey", "readonly": True, "value": "secret-from-charger"},
            {"key": "CustomCredential", "readonly": False, "value": "another-secret"},
        ], "unknown_key": ["VendorX"],
    }, direction="in")
    data = export_contract(tmp_path)["data"]
    assert data["cursor"]["next"] == 2
    assert len(data["configuration_snapshots"]) == 1
    [snapshot] = data["configuration_snapshots"]
    assert snapshot["source_event_id"] == 2
    assert snapshot["coverage"] == "full"
    assert snapshot["configuration"][0]["value"] == "300"
    assert snapshot["configuration"][1]["value"] == "[REDACTED]"
    assert snapshot["configuration"][2]["value"] == "[REDACTED]"
    assert snapshot["unknown"] == ["VendorX"]
    assert "secret-from-charger" not in repr(data)


def test_historical_backfill_is_bounded_and_keeps_full_and_partial_distinct(tmp_path):
    store = EventStore(tmp_path)
    store.record_ocpp("cp-a", "GetConfiguration", {"key": ["HeartbeatInterval"]}, direction="out")
    store.record_ocpp("cp-a", "GetConfiguration", {
        "configuration_key": [{"key": "HeartbeatInterval", "readonly": False, "value": "30"}],
        "unknown_key": [],
    }, direction="in")
    store.record_ocpp("cp-a", "GetConfiguration", {}, direction="out")
    store.record_ocpp("cp-a", "GetConfiguration", {
        "configuration_key": [{"key": "HeartbeatInterval", "readonly": False, "value": "60"}],
        "unknown_key": [],
    }, direction="in")
    first = export_configuration_history(tmp_path, after=0, limit=1)["data"]
    second = export_configuration_history(tmp_path, after=first["cursor"]["next"], limit=1)["data"]
    assert first["cursor"] == {"after": 0, "next": 2, "more": True}
    assert second["cursor"] == {"after": 2, "next": 4, "more": False}
    assert first["configuration_snapshots"][0]["coverage"] == "partial"
    assert second["configuration_snapshots"][0]["coverage"] == "full"
    assert export_configuration_history(tmp_path, after=0, limit=1)["data"] == first
    assert export_contract(tmp_path)["data"]["cursor"]["next"] == 4


def test_malformed_and_unpaired_responses_are_not_misclassified(tmp_path):
    store = EventStore(tmp_path)
    store.record_ocpp("cp-a", "GetConfiguration", {
        "configuration_key": [{"key": "FeatureX", "readonly": True, "value": "1"}],
        "unknown_key": [],
    }, direction="in")
    store.record_ocpp("cp-a", "GetConfiguration", {
        "configuration_key": [{"key": "Bad", "readonly": "false", "value": "bad"}],
    }, direction="in")
    result = export_configuration_history(tmp_path)["data"]
    assert [s["source_event_id"] for s in result["configuration_snapshots"]] == [1]
    assert result["configuration_snapshots"][0]["coverage"] == "unknown"
    assert result["cursor"]["next"] == 2
