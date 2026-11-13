from pathlib import Path

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.status import appliance_status, format_status


def _start(events: EventStore, connector_id: int) -> int:
    return events.commit_start(
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
    transaction_id = _start(events, 1)

    charger = appliance_status(tmp_path)["chargers"][0]

    assert charger.status == "Charging"
    assert [(item.connector_id, item.status) for item in charger.connectors] == [
        (1, "Charging"),
        (2, "Available"),
    ]
    assert charger.connectors[0].transaction_id == transaction_id
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
    transaction_id = _start(events, 1)

    charger = appliance_status(tmp_path)["chargers"][0]

    assert charger.status == "Available"
    assert charger.transaction_id == transaction_id
    assert charger.availability == "unknown"  # No live server connection established
    assert charger.activity == "unknown"
    assert [(item.connector_id, item.status) for item in charger.connectors] == [(1, "Preparing"), (2, "Available")]


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
    transaction_id = _start(events, 1)

    text = format_status(appliance_status(tmp_path), charger_id="charger-a")

    assert "Connector 1: Charging" in text
    assert f"Transaction: {transaction_id}" in text
    assert "Connector 2: Available" in text


def test_independent_customer_availability_and_operator_activity():
    from ocpp_csms.status import ConnectorStatus, operational_state
    connectors = [
        ConnectorStatus(connector_id=1, status="Charging", error_code=None,
                        transaction_id=12, id_tag="card", started_at=None),
        ConnectorStatus(connector_id=2, status="Available", error_code=None,
                        transaction_id=None, id_tag=None, started_at=None),
    ]
    assert operational_state(connectors) == ("available", "active")
    connectors[0].status = "Preparing"
    assert operational_state(connectors) == ("available", "unknown")
    connectors[0].status = "Unavailable"
    assert operational_state(connectors) == ("available", "inactive")


def test_status_table_has_independent_connector_columns(tmp_path: Path):
    events = EventStore(tmp_path)
    events.record_runtime("server_started")
    events.record_runtime("charger_connected", charger_id="charger-a")
    events.record_connector_status(
        "charger-a", {"connector_id": 1, "status": "Charging", "timestamp": "2026-10-01T15:01:00Z"},
    )
    events.record_connector_status(
        "charger-a", {"connector_id": 2, "status": "Available", "timestamp": "2026-10-01T15:02:00Z"},
    )
    from ocpp_csms.runtime import write_pid
    write_pid(tmp_path)
    data = appliance_status(tmp_path)
    text = format_status(data)
    assert "State C1" in text and "State C2" in text
    assert "Charging" in text and "Available" in text
    header = next(line for line in text.splitlines() if line.startswith("ID "))
    assert "Charging" not in header
    assert "Last seen" in header
    assert "charger-a" in data["active_chargers"]


def test_unreported_connector_defaults_independently(tmp_path: Path):
    events = EventStore(tmp_path)
    events.record_runtime("server_started")
    events.record_runtime("charger_connected", charger_id="charger-a")
    events.record_connector_status(
        "charger-a", {"connector_id": 1, "status": "Faulted", "timestamp": "2026-10-01T15:01:00Z"},
    )
    _start(events, 2)
    from ocpp_csms.runtime import write_pid
    write_pid(tmp_path)
    data = appliance_status(tmp_path)
    connector = {c.connector_id: c for c in data["chargers"][0].connectors}
    assert connector[1].derived_status == "Faulted"
    assert connector[2].derived_status == "Unknown"
    assert "State C2" in format_status(data)


def test_no_fabricated_second_connector(tmp_path: Path):
    events = EventStore(tmp_path)
    events.record_connector_status(
        "charger-a", {"connector_id": 1, "status": "Available", "timestamp": "2026-10-01T15:00:00Z"},
    )
    text = format_status(appliance_status(tmp_path))
    assert "State C1" in text
    assert "State C2" not in text


def test_status_table_shows_observed_connector_zero_independently(tmp_path: Path):
    events = EventStore(tmp_path)
    events.record_connector_status("charger-a", {
        "connector_id": 0, "status": "Available", "timestamp": "2026-10-22T14:58:39Z",
    })
    events.record_connector_status("charger-a", {
        "connector_id": 1, "status": "Faulted", "timestamp": "2026-10-22T14:58:38Z",
    })
    text = format_status(appliance_status(tmp_path))
    header = next(line for line in text.splitlines() if line.startswith("ID "))
    row = next(line for line in text.splitlines() if line.startswith("charger-a"))
    assert "State C0" in header and "State C1" in header
    assert header.index("State C0") < header.index("State C1")
    assert "Available" in row and "Faulted" in row
    assert "State C2" not in header
