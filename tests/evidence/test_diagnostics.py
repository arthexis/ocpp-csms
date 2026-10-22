import pytest

from ocpp_csms.evidence.diagnostics import events_between, explain, format_events
from ocpp_csms.evidence.store import EventStore


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


def test_events_filter_multiple_transactions_before_limit(tmp_path):
    store = EventStore(tmp_path)
    store.record_ocpp("charger-a", "MeterValues", {}, transaction_id=240)
    store.record_ocpp("charger-a", "MeterValues", {}, transaction_id=241)
    store.record_ocpp("charger-a", "MeterValues", {}, transaction_id=242)
    store.record_ocpp("charger-a", "Heartbeat", {})
    rows = events_between(tmp_path, transaction_id=(240, 241), limit=2)
    assert {row["transaction_id"] for row in rows} == {240, 241}
    assert len(rows) == 2
    assert len(events_between(tmp_path, transaction_id=240)) == 1


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


def test_boot_notification_is_distinct_from_connection_and_has_details(tmp_path):
    store = EventStore(tmp_path)
    store.record_runtime("charger_connected", charger_id="charger-a")
    store.record_ocpp("charger-a", "BootNotification", {
        "charge_point_vendor": "Acme",
        "charge_point_model": "Dual",
        "firmware_version": "1.2.3",
        "charge_point_serial_number": "SN123",
    })
    store.record_ocpp("charger-a", "BootNotification", {
        "status": "Accepted", "interval": 60, "current_time": "2026-10-15T15:00:00Z",
    }, direction="out")
    rows = events_between(tmp_path)
    compact = format_events(rows)
    detailed = format_events(rows, detail=True)
    assert "charger connected" in compact
    assert "BootNotification" in compact
    assert "→ BootNotification status=Accepted interval=60" in compact
    assert "charge_point_vendor=Acme" not in compact
    assert "charge_point_vendor=Acme" in detailed
    assert "charge_point_model=Dual" in detailed
    assert "firmware_version=1.2.3" in detailed
    assert "charge_point_serial_number=SN123" in detailed
    assert "interval=60" in detailed
    assert "current_time=2026-10-15T15:00:00Z" in detailed


def test_connection_without_boot_notification_does_not_invent_one(tmp_path):
    store = EventStore(tmp_path)
    store.record_runtime("charger_connected", charger_id="charger-a")
    assert "BootNotification" not in format_events(events_between(tmp_path), detail=True)


def test_connection_detail_shows_only_recorded_safe_fields(tmp_path):
    store = EventStore(tmp_path)
    store.record_runtime("charger_connected", charger_id="charger-a", details={
        "path": "/ocpp/charger-a", "subprotocol": "ocpp1.6",
        "remote": "192.0.2.10:54321", "user_agent": "ChargerWS/1.0",
        "requested_subprotocols": "ocpp1.6", "authorization": "secret",
    })
    rows = events_between(tmp_path)
    compact = format_events(rows)
    detail = format_events(rows, detail=True)
    assert "user_agent" not in compact
    assert 'path="/ocpp/charger-a"' in detail
    assert 'subprotocol="ocpp1.6"' in detail
    assert 'remote="192.0.2.10:54321"' in detail
    assert 'user_agent="ChargerWS/1.0"' in detail
    assert 'requested_subprotocols="ocpp1.6"' in detail
    assert "secret" not in detail


def test_old_connection_record_still_expands_existing_fields(tmp_path):
    store = EventStore(tmp_path)
    store.record_runtime("charger_connected", charger_id="charger-a",
                         details={"path": "/charger-a", "subprotocol": None})
    assert 'path="/charger-a"' in format_events(events_between(tmp_path), detail=True)
