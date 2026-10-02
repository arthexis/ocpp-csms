from pathlib import Path

from ocpp_csms.events import EventStore
from ocpp_csms.status import appliance_status, format_status


def _start(events: EventStore, transaction_id: int, connector_id: int) -> None:
    events.record_transaction_start(
        transaction_id,
        "charger-a",
        {
            "connector_id": connector_id,
            "id_tag": f"card-{connector_id}",
            "meter_start": 1000,
            "timestamp": f"2026-10-01T15:00:0{connector_id}Z",
        },
    )


def test_status_keeps_connectors_separate(tmp_path: Path):
    events = EventStore(tmp_path)
    events.record_connector_status(
        "charger-a",
        {"connector_id": 1, "status": "Charging", "timestamp": "2026-10-01T15:01:00Z"},
    )
    events.record_connector_status(
        "charger-a",
        {"connector_id": 2, "status": "Available", "timestamp": "2026-10-01T15:02:00Z"},
    )
    _start(events, 7, 1)

    charger = appliance_status(tmp_path)["chargers"][0]

    assert charger.status == "Charging"
    assert [(item.connector_id, item.status) for item in charger.connectors] == [
        (1, "Charging"),
        (2, "Available"),
    ]
    assert charger.connectors[0].transaction_id == 7
    assert charger.connectors[1].transaction_id is None


def test_open_transaction_uses_its_connector_state(tmp_path: Path):
    events = EventStore(tmp_path)
    events.record_connector_status(
        "charger-a",
        {"connector_id": 1, "status": "Preparing", "timestamp": "2026-10-01T15:01:00Z"},
    )
    events.record_connector_status(
        "charger-a",
        {"connector_id": 2, "status": "Available", "timestamp": "2026-10-01T15:02:00Z"},
    )
    _start(events, 7, 1)

    charger = appliance_status(tmp_path)["chargers"][0]

    assert charger.status == "Preparing"
    assert charger.transaction_id == 7


def test_detailed_status_prints_each_connector(tmp_path: Path):
    events = EventStore(tmp_path)
    events.record_connector_status(
        "charger-a",
        {"connector_id": 1, "status": "Charging", "timestamp": "2026-10-01T15:01:00Z"},
    )
    events.record_connector_status(
        "charger-a",
        {"connector_id": 2, "status": "Available", "timestamp": "2026-10-01T15:02:00Z"},
    )
    _start(events, 7, 1)

    text = format_status(appliance_status(tmp_path), charger_id="charger-a")

    assert "Connector 1: Charging" in text
    assert "Transaction: 7" in text
    assert "Connector 2: Available" in text
