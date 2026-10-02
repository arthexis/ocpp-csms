from pathlib import Path

from ocpp_csms.events import EventStore
from ocpp_csms.status import appliance_status, format_status


def test_status_uses_derived_connector_and_transaction_state(tmp_path: Path):
    events = EventStore(tmp_path)
    events.record_runtime("server_started")
    events.record_runtime("charger_connected", charger_id="charger-a")
    events.record_connector_status(
        "charger-a",
        {
            "connector_id": 1,
            "status": "Charging",
            "error_code": "NoError",
            "timestamp": "2026-10-01T15:00:01Z",
        },
    )
    events.record_transaction_start(
        7,
        "charger-a",
        {
            "connector_id": 1,
            "id_tag": "card-a",
            "meter_start": 1000,
            "timestamp": "2026-10-01T15:00:00Z",
        },
    )

    data = appliance_status(tmp_path)
    text = format_status(data)
    charger = format_status(data, charger_id="charger-a")

    assert data["server"] == "running"
    assert "charger-a" in text
    assert "Charging" in text
    assert "Transaction: 7" in charger
    assert "RFID: card-a" in charger


def test_charging_filter_requires_open_transaction(tmp_path: Path):
    events = EventStore(tmp_path)
    events.record_runtime("charger_connected", charger_id="charger-a")
    events.record_runtime("charger_connected", charger_id="charger-b")
    events.record_connector_status(
        "charger-a",
        {"connector_id": 1, "status": "Charging", "timestamp": "2026-10-01T15:00:00Z"},
    )
    events.record_connector_status(
        "charger-b",
        {"connector_id": 1, "status": "Charging", "timestamp": "2026-10-01T15:00:00Z"},
    )
    events.record_transaction_start(
        7,
        "charger-a",
        {
            "connector_id": 1,
            "id_tag": "card-a",
            "meter_start": 1000,
            "timestamp": "2026-10-01T15:00:01Z",
        },
    )

    text = format_status(appliance_status(tmp_path), charging_only=True)

    assert "charger-a" in text
    assert "charger-b" not in text
