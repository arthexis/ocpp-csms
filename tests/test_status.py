import os
from pathlib import Path

from ocpp_csms.events import EventStore
from ocpp_csms.runtime import PID_FILENAME, write_pid
from ocpp_csms.status import appliance_status, format_status


def test_status_uses_derived_connector_and_transaction_state(tmp_path: Path):
    events = EventStore(tmp_path)
    write_pid(tmp_path)
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


def test_historical_start_event_does_not_imply_running(tmp_path: Path):
    events = EventStore(tmp_path)
    events.record_runtime("server_started")

    data = appliance_status(tmp_path)

    assert data["server"] == "stopped"
    assert data["started_at"] is None


def test_live_pid_reports_running_without_database(tmp_path: Path):
    write_pid(tmp_path)

    data = appliance_status(tmp_path)

    assert data["server"] == "running"
    assert data["database"] == "missing"


def test_dead_pid_reports_stopped(tmp_path: Path):
    (tmp_path / PID_FILENAME).write_text("999999999\n", encoding="utf-8")

    assert appliance_status(tmp_path)["server"] == "stopped"


def test_current_pid_marker_is_live(tmp_path: Path):
    (tmp_path / PID_FILENAME).write_text(f"{os.getpid()}\n", encoding="utf-8")

    assert appliance_status(tmp_path)["server"] == "running"


def test_detailed_status_shows_latest_negotiated_subprotocol(tmp_path: Path):
    events = EventStore(tmp_path)
    events.record_runtime(
        "charger_connected",
        charger_id="charger-a",
        details={
            "path": "/ocpp/charger-a",
            "charge_point_id": "charger-a",
            "subprotocol": None,
        },
    )
    events.record_runtime(
        "charger_connected",
        charger_id="charger-a",
        details={
            "path": "/ocpp/charger-a",
            "charge_point_id": "charger-a",
            "subprotocol": "ocpp1.6",
        },
    )

    data = appliance_status(tmp_path)

    assert data["chargers"][0].subprotocol == "ocpp1.6"
    assert "Protocol: ocpp1.6" in format_status(data, charger_id="charger-a")
    assert "Protocol:" not in format_status(data)


def test_detailed_status_shows_missing_subprotocol_without_rejecting_charger(tmp_path: Path):
    events = EventStore(tmp_path)
    events.record_runtime(
        "charger_connected",
        charger_id="charger-a",
        details={
            "path": "/ocpp/charger-a",
            "charge_point_id": "charger-a",
            "subprotocol": None,
        },
    )

    data = appliance_status(tmp_path)
    charger = data["chargers"][0]

    assert charger.connected is True
    assert charger.subprotocol is None
    assert "Protocol: not negotiated" in format_status(data, charger_id="charger-a")
