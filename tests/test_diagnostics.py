import pytest

from ocpp_csms.diagnostics import events_between, explain, format_events
from ocpp_csms.events import EventStore


def test_events_merge_runtime_and_ocpp(tmp_path):
    store = EventStore(tmp_path)
    store.record_runtime("charger_connected", charger_id="charger-a")
    store.record_ocpp("charger-a", "Authorize", {"id_tag": "card-a"})
    store.record_ocpp("charger-a", "Authorize", {"idTagInfo": {"status": "Accepted"}}, direction="out")

    rows = events_between(tmp_path, charger_id="charger-a")
    text = format_events(rows)

    assert [row["action"] for row in rows] == ["charger_connected", "Authorize", "Authorize"]
    assert "Authorize RFID card-a" in text
    assert "→ Authorize Accepted" in text


def test_events_filter_by_charger(tmp_path):
    store = EventStore(tmp_path)
    store.record_ocpp("charger-a", "Heartbeat", {})
    store.record_ocpp("charger-b", "Heartbeat", {})

    rows = events_between(tmp_path, charger_id="charger-b")
    assert len(rows) == 1
    assert rows[0]["charger_id"] == "charger-b"


def test_explain_supports_at_and_explicit_range(tmp_path):
    store = EventStore(tmp_path)
    store.record_ocpp("charger-a", "Authorize", {"id_tag": "card-a"})
    store.record_ocpp("charger-a", "Authorize", {"idTagInfo": {"status": "Accepted"}}, direction="out")
    rows = events_between(tmp_path, charger_id="charger-a")

    centered = explain(tmp_path, "charger-a", at=rows[0]["occurred_at"])
    ranged = explain(
        tmp_path,
        "charger-a",
        since=rows[0]["occurred_at"],
        until=rows[-1]["occurred_at"],
    )

    assert "→ Authorize Accepted" in centered
    assert "from" in ranged


def test_reversed_bounds_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="--since must be earlier"):
        events_between(tmp_path, since="2026-10-01T16:00:00Z", until="2026-10-01T15:00:00Z")


def test_compact_responses_preserve_explicit_outcomes(tmp_path):
    store = EventStore(tmp_path)
    store.record_ocpp("charger-a", "Heartbeat", {})
    store.record_ocpp("charger-a", "Heartbeat", {}, direction="out")
    store.record_ocpp("charger-a", "Authorize", {"idTagInfo": {"status": "Rejected"}}, direction="out")
    store.record_ocpp("charger-a", "StatusNotification", {}, direction="out")
    lines = format_events(events_between(tmp_path, charger_id="charger-a"))
    assert "Heartbeat OK" in lines
    assert "Authorize Rejected" in lines
    assert "StatusNotification OK" in lines
    # Chunk one doesn't merge or silently remove individual events.
    assert len(lines.splitlines()) == 4


def test_compact_status_and_stop_retain_operational_details(tmp_path):
    store = EventStore(tmp_path)
    store.record_ocpp("charger-a", "StatusNotification", {
        "connector_id": 1, "status": "Faulted",
        "error_code": "InternalError", "info": "EmergencyStop"
    })
    store.record_ocpp("charger-a", "StopTransaction", {
        "transaction_id": 52, "reason": "EmergencyStop",
    })
    text = format_events(events_between(tmp_path, charger_id="charger-a"))
    assert "StatusNotification C1 Faulted InternalError EmergencyStop" in text
    assert "StopTransaction tx=52 reason=EmergencyStop" in text
