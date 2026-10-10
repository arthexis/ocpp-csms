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
    assert "Heartbeat requests (responses not established) ×2" in output
    assert "→ Heartbeat responses ×2" in output
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
    assert "Heartbeat requests (responses not established) ×2" in output


def test_limited_window_counts_only_observed_rows():
    rows = [row(5, "Heartbeat"), row(6, "Heartbeat")]
    output = format_events(rows)
    assert "×2" in output
    assert "14:00:05Z" in output and "14:00:06Z" in output
    assert "×3" not in output


def test_unanswered_heartbeat_run_is_not_claimed_as_success():
    output = format_events([row(1, "Heartbeat"), row(2, "Heartbeat"), row(3, "Heartbeat")])
    assert "requests (responses not established) ×3" in output
    assert "responses ×3" not in output


def test_unusual_response_details_remain_visible():
    output = format_events([
        row(1, "Heartbeat", {"current_time": "2026-10-09T14:00:01Z",
                             "error": "unexpected"}, direction="out"),
        row(2, "Heartbeat", {"current_time": "2026-10-09T14:00:02Z"},
            direction="out"),
    ])
    assert 'extra={"error": "unexpected"}' in output
    assert "×2" not in output


def test_connector_and_transaction_boundaries():
    status = {"connector_id": 1, "status": "Available", "error_code": "NoError"}
    second = dict(status, connector_id=2)
    rows = [row(1, "StatusNotification", status),
            row(2, "StatusNotification", second),
            row(3, "StartTransaction", {"connector_id": 1, "transaction_id": 42}),
            row(4, "StatusNotification", status)]
    output = format_events(rows)
    assert "×" not in output
    assert "StartTransaction" in output


def test_real_charger_alternating_camelcase_heartbeat_exchanges():
    rows = []
    for n in range(1, 18):
        rows.append(row(n * 2 - 1, "Heartbeat"))
        rows.append(row(n * 2, "Heartbeat", {"currentTime": "2026-10-10T00:07:31Z"}, direction="out"))
    output = format_events(rows)
    assert output.count("Heartbeat ↔") == 1
    assert "×17" in output
    assert "extra=" not in output
    assert format_events(rows, verbose=True).count("payload=") == 34


def test_missing_reply_breaks_observed_exchange_run():
    rows = [row(1, "Heartbeat"), row(2, "Heartbeat", {"currentTime": "now"}, direction="out"),
            row(3, "Heartbeat"), row(4, "Heartbeat"),
            row(5, "Heartbeat", {"currentTime": "now"}, direction="out")]
    output = format_events(rows)
    assert output.count("Heartbeat ↔") == 2
    assert "responses not established" in output


def test_anomalous_response_interrupts_pairs():
    rows = [row(1, "Heartbeat"),
            row(2, "Heartbeat", {"currentTime": "now", "error": "unexpected"}, direction="out"),
            row(3, "Heartbeat"), row(4, "Heartbeat", {"currentTime": "now"}, direction="out")]
    output = format_events(rows)
    assert "extra=" in output
    assert output.count("Heartbeat ↔") == 1
