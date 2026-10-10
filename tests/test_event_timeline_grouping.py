"""Regression checks for the conservative human-readable event grouping."""
import json

from ocpp_csms.diagnostics import format_events


def row(n, action, payload=None, *, direction="in", charger="CP1", kind="ocpp"):
    return {
        "id": n, "occurred_at": f"2026-10-09T14:00:{n:02d}Z",
        "charger_id": charger, "kind": kind, "action": action,
        "direction": direction, "transaction_id": None, "id_tag": None,
        "payload": json.dumps(payload if payload is not None else {}),
    }


def test_heartbeat_requests_and_responses_not_paired():
    rows = [
        row(1, "Heartbeat"), row(2, "Heartbeat"),
        row(3, "Heartbeat", {"current_time": "2026-10-09T14:00:03Z"}, direction="out"),
        row(4, "Heartbeat", {"current_time": "2026-10-09T14:00:04Z"}, direction="out"),
    ]
    output = format_events(rows)
    assert "Heartbeat ×2" in output
    assert "→ Heartbeat ack ×2" in output
    assert "4 responses" not in output


def test_group_boundaries_and_charger_identity():
    rows = [
        row(1, "Heartbeat"), row(2, "Heartbeat", charger="CP2"),
        row(3, "Heartbeat"), row(4, "charger_disconnected", kind="runtime"),
        row(5, "Heartbeat"),
    ]
    output = format_events(rows)
    assert "×" not in output
    assert "charger disconnected" in output


def test_benign_status_repeats_and_fault_interruptions():
    benign = {"connector_id": 1, "status": "Preparing", "error_code": "NoError"}
    fault = {"connector_id": 1, "status": "Faulted",
             "error_code": "InternalError", "info": "EmergencyStop"}
    rows = [
        row(1, "StatusNotification", benign),
        row(2, "StatusNotification", benign),
        row(3, "StatusNotification", fault),
        row(4, "StatusNotification", benign),
    ]
    output = format_events(rows)
    assert "StatusNotification C1 Preparing ×2" in output
    assert "Faulted InternalError EmergencyStop" in output
    assert output.count("StatusNotification C1 Preparing") == 2


def test_anomalous_heartbeat_and_missing_response_remain_visible():
    rows = [row(1, "Heartbeat"), row(2, "Heartbeat", {"unexpected": 1}),
            row(3, "Heartbeat"), row(4, "Heartbeat")]
    output = format_events(rows)
    assert output.count("Heartbeat") == 3
    assert "Heartbeat ×2" in output


def test_limited_window_counts_only_observed_rows():
    rows = [row(5, "Heartbeat"), row(6, "Heartbeat")]
    output = format_events(rows)
    assert "×2" in output
    assert "14:00:05Z" in output and "14:00:06Z" in output
    assert "×3" not in output
