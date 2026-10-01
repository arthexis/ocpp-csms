from pathlib import Path

from ocpp_csms.events import EventStore
from ocpp_csms.status import appliance_status, format_status


def test_status_reduces_runtime_and_ocpp_events(tmp_path: Path):
    events = EventStore(tmp_path)
    events.record_runtime("server_started")
    events.record_runtime("charger_connected", charger_id="charger-a")
    events.record_ocpp(
        "charger-a",
        "StatusNotification",
        {"status": "Charging", "error_code": "NoError"},
    )
    events.record_ocpp(
        "charger-a",
        "StartTransaction",
        {"id_tag": "card-a", "timestamp": "2026-10-01T15:00:00Z"},
        transaction_id=7,
    )

    data = appliance_status(tmp_path)
    text = format_status(data)
    charger = format_status(data, charger_id="charger-a")

    assert data["server"] == "running"
    assert "charger-a" in text
    assert "Charging" in text
    assert "Transaction: 7" in charger
    assert "RFID: card-a" in charger


def test_charging_filter_hides_idle_chargers(tmp_path: Path):
    events = EventStore(tmp_path)
    events.record_runtime("charger_connected", charger_id="charger-a")
    events.record_runtime("charger_connected", charger_id="charger-b")
    events.record_ocpp("charger-a", "StatusNotification", {"status": "Available"})
    events.record_ocpp("charger-b", "StatusNotification", {"status": "Charging"})

    data = appliance_status(tmp_path)
    text = format_status(data, charging_only=True)

    assert "charger-b" in text
    assert "charger-a" not in text
