"""Regression checks for the conservative human-readable event grouping."""
import json

from ocpp_csms.evidence.diagnostics import format_events


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
    assert "Heartbeat requests (responses not established)" in output
    assert "Heartbeat ↔ ×1" in output
    assert "→ Heartbeat responses" in output
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
    assert "extra=1" in output
    assert 'extra={"error": "unexpected"}' in format_events([row(1, "Heartbeat", {"current_time": "now", "error": "unexpected"}, direction="out")], detail=True)
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
    assert "Heartbeat requests (responses not established)" in output
    assert "→ Heartbeat responses" not in output


def test_anomalous_response_interrupts_pairs():
    rows = [row(1, "Heartbeat"),
            row(2, "Heartbeat", {"currentTime": "now", "error": "unexpected"}, direction="out"),
            row(3, "Heartbeat"), row(4, "Heartbeat", {"currentTime": "now"}, direction="out")]
    output = format_events(rows)
    assert "extra=1" in output
    assert 'extra={"error": "unexpected"}' in format_events(rows, detail=True)
    assert output.count("Heartbeat ↔") == 1


def test_configuration_response_extra_count_and_detail():
    rows = [row(1, "GetConfiguration", {"key": ["LocalAuthListEnabled", "AuthorizationCacheEnabled"]}, direction="out")]
    compact = format_events(rows)
    assert "GetConfiguration response extra=1" in compact
    assert "LocalAuthListEnabled" not in compact
    detailed = format_events(rows, detail=True)
    assert 'extra={"key": ["LocalAuthListEnabled", "AuthorizationCacheEnabled"]}' in detailed
    assert "payload=" not in detailed
    assert "payload=" in format_events(rows, verbose=True)


def test_repeated_requests_then_response_preserves_unmatched_evidence():
    rows = [
        row(1, "Heartbeat"),
        row(2, "Heartbeat"),
        row(3, "Heartbeat", {"currentTime": "now"}, direction="out"),
        row(4, "Heartbeat"),
        row(5, "Heartbeat", {"currentTime": "later"}, direction="out"),
    ]
    output = format_events(rows)
    assert "Heartbeat requests (responses not established)" in output
    assert output.count("Heartbeat ↔") == 1
    assert "Heartbeat ↔ ×2" in output
    assert "→ Heartbeat responses" not in output


def test_orphan_heartbeat_response_is_not_assumed_paired():
    rows = [row(1, "charger_connected", kind="runtime"),
            row(2, "Heartbeat", {"currentTime": "now"}, direction="out")]
    output = format_events(rows)
    assert "→ Heartbeat responses" in output
    assert "Heartbeat ↔" not in output


def meter(n, connector=1, tx=42, *, response=False, charger="CP1", payload=None):
    if payload is None:
        payload = {} if response else {
            "connector_id": connector, "transaction_id": tx,
            "meter_value": [{"timestamp": "2026-10-09T14:00:00Z",
                             "sampled_value": [{"value": str(n)}]}],
        }
    result = row(n, "MeterValues", payload, direction="out" if response else "in",
                 charger=charger)
    if not response:
        result["transaction_id"] = tx
    return result


def test_meter_exchange_grouping_and_unmodified_verbose():
    rows = [meter(1), meter(2, response=True), meter(3), meter(4, response=True)]
    compact = format_events(rows)
    assert "MeterValues C1 tx=42 ↔ ×2" in compact
    assert compact.count("MeterValues") == 1
    verbose = format_events(rows, verbose=True)
    assert verbose.count("MeterValues") == 4
    assert verbose.count("payload=") == 4
    assert [r["payload"] for r in rows] == [
        r["payload"] for r in rows
    ]  # formatter must not alter underlying evidence


def test_meter_connector_transaction_and_charger_boundaries():
    rows = [meter(1), meter(2, response=True),
            meter(3, connector=2), meter(4, response=True),
            meter(5, connector=2, tx=43), meter(6, response=True),
            meter(7, connector=2, tx=43, charger="CP2"),
            meter(8, response=True, charger="CP2")]
    output = format_events(rows)
    assert output.count("↔ ×1") == 4
    assert "C1 tx=42" in output and "C2 tx=43" in output


def test_meter_interruptions_and_missing_responses():
    rows = [meter(1), meter(2, response=True),
            meter(3), row(4, "StatusNotification",
                          {"connector_id": 1, "status": "Faulted", "error_code": "ReaderFailure"}),
            meter(5, response=True), meter(6), meter(7, response=True)]
    output = format_events(rows)
    assert output.count("MeterValues C1 tx=42 ↔ ×1") == 2
    assert "Faulted ReaderFailure" in output
    assert "→ MeterValues response" in output
    assert output.count("MeterValues C1 tx=42") == 3


def test_meter_anomalous_and_malformed_frames_not_grouped():
    invalid = meter(3, response=True, payload={"unexpected": "bad"})
    malformed = meter(5)
    malformed["payload"] = "not-json"
    rows = [meter(1), meter(2, response=True), invalid,
            meter(4), malformed, meter(6, response=True)]
    output = format_events(rows)
    assert output.count("↔ ×1") == 1
    assert "extra=1" in output
    assert "[invalid payload]" in output
    assert "→ MeterValues response" in output


def test_meter_window_edges_do_not_infer_unseen_pairs():
    rows = [meter(2, response=True), meter(3), meter(4, response=True), meter(5)]
    output = format_events(rows)
    assert output.count("↔ ×1") == 1
    assert output.count("→ MeterValues response") == 1
    assert output.count("MeterValues C1 tx=42") == 2
    assert "14:00:02Z" in output and "14:00:05Z" in output


def test_meter_extra_fields_or_mismatched_transaction_are_not_grouped():
    extra = meter(1, payload={"connector_id": 1, "transaction_id": 42,
                              "meter_value": [{"timestamp": "now"}], "vendor": 1})
    mismatch = meter(3, payload={"connector_id": 1, "transaction_id": 43,
                                 "meter_value": [{"timestamp": "now"}]})
    output = format_events([extra, meter(2, response=True),
                            mismatch, meter(4, response=True)])
    assert "↔" not in output
    assert output.count("→ MeterValues response") == 2
