import pytest

from ocpp_csms.diagnostics import events_between, explain, explain_between, format_events
from ocpp_csms.events import EventStore


def test_events_merge_runtime_and_ocpp_in_time_order(tmp_path):
    store = EventStore(tmp_path)
    store.record_runtime("charger_connected", charger_id="charger-a")
    store.record_ocpp("charger-a", "Authorize", {"id_tag": "card-a"})
    store.record_ocpp(
        "charger-a",
        "Authorize",
        {"idTagInfo": {"status": "Accepted"}},
        direction="out",
    )

    rows = events_between(tmp_path, charger_id="charger-a")
    text = format_events(rows)

    assert [row.action for row in rows] == [
        "charger_connected",
        "Authorize",
        "Authorize",
    ]
    assert "Authorize RFID card-a" in text
    assert "→ Authorize Accepted" in text


def test_events_filter_by_charger(tmp_path):
    store = EventStore(tmp_path)
    store.record_ocpp("charger-a", "Heartbeat", {})
    store.record_ocpp("charger-b", "Heartbeat", {})

    rows = events_between(tmp_path, charger_id="charger-b")

    assert len(rows) == 1
    assert rows[0].charger_id == "charger-b"


def test_explain_reports_recorded_acceptance_fault_and_disconnect(tmp_path):
    store = EventStore(tmp_path)
    store.record_runtime("charger_connected", charger_id="charger-a")
    store.record_ocpp("charger-a", "Authorize", {"id_tag": "card-a"})
    store.record_ocpp(
        "charger-a",
        "Authorize",
        {"idTagInfo": {"status": "Accepted"}},
        direction="out",
    )
    store.record_ocpp(
        "charger-a",
        "StatusNotification",
        {"status": "Faulted", "error_code": "GroundFailure"},
    )
    store.record_runtime("charger_disconnected", charger_id="charger-a")

    rows = events_between(tmp_path, charger_id="charger-a")
    at = rows[1].occurred_at
    text = explain(tmp_path, "charger-a", at, minutes=10)

    assert "CSMS authorization reply: Accepted." in text
    assert "No StartTransaction was recorded in this window." in text
    assert "Charger reported Faulted / GroundFailure." in text
    assert "Charger disconnected during this window." in text


def test_explain_between_uses_explicit_bounds(tmp_path):
    store = EventStore(tmp_path)
    store.record_ocpp("charger-a", "Authorize", {"id_tag": "card-a"})
    store.record_ocpp(
        "charger-a",
        "Authorize",
        {"idTagInfo": {"status": "Accepted"}},
        direction="out",
    )

    rows = events_between(tmp_path, charger_id="charger-a")
    since = rows[0].occurred_at
    until = rows[-1].occurred_at
    text = explain_between(tmp_path, "charger-a", since, until)

    assert f"from {since} until {until}" in text
    assert "Authorization request was received by the CSMS." in text
    assert "CSMS authorization reply: Accepted." in text


def test_explain_between_rejects_reversed_bounds(tmp_path):
    with pytest.raises(ValueError, match="--since must be earlier"):
        explain_between(
            tmp_path,
            "charger-a",
            "2026-10-01T16:00:00Z",
            "2026-10-01T15:00:00Z",
        )
