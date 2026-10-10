import asyncio
import os
from pathlib import Path

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.runtime import PID_FILENAME, write_pid
from ocpp_csms.status import appliance_status, format_status
from ocpp_csms.transactions.archive import TransactionArchive


def start_payload(*, connector=1, id_tag="card-a", timestamp="2026-10-01T15:00:00Z"):
    return {
        "connector_id": connector,
        "id_tag": id_tag,
        "meter_start": 1000,
        "timestamp": timestamp,
    }


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
    events.record_transaction_start(7, "charger-a", start_payload())

    data = appliance_status(tmp_path)
    charger = data["chargers"][0]

    assert data["server"] == "running"
    assert charger.charger_id == "charger-a"
    assert charger.status == "Charging"
    assert charger.transaction_id == 7
    assert charger.id_tag == "card-a"


def test_charging_filter_uses_transaction_query_activity(tmp_path: Path):
    events = EventStore(tmp_path)
    archive = TransactionArchive(tmp_path)
    for charger in ("charger-a", "charger-b"):
        events.record_runtime("charger_connected", charger_id=charger)
        events.record_connector_status(
            charger,
            {"connector_id": 1, "status": "Charging", "timestamp": "2026-10-01T15:00:00Z"},
        )

    active = asyncio.run(archive.start("charger-a", start_payload()))
    finished = asyncio.run(
        archive.start("charger-b", start_payload(timestamp="2026-10-01T14:00:00Z"))
    )
    asyncio.run(
        archive.stop(
            "charger-b",
            {
                "transaction_id": finished,
                "meter_stop": 1200,
                "timestamp": "2026-10-01T14:30:00Z",
            },
        )
    )

    data = appliance_status(tmp_path)

    assert data["active_chargers"] == ["charger-a"]
    assert active != finished
    filtered = format_status(data, charging_only=True)
    assert "charger-a" in filtered
    assert "charger-b" not in filtered


def test_historical_start_event_does_not_imply_running(tmp_path: Path):
    events = EventStore(tmp_path)
    events.record_runtime("server_started")

    data = appliance_status(tmp_path)

    assert data["server"] == "stopped"
    assert data["started_at"] is None


def test_running_server_ignores_connected_state_from_previous_process(tmp_path: Path):
    events = EventStore(tmp_path)
    events.record_runtime("charger_connected", charger_id="charger-a")
    events.record_runtime("server_started")
    write_pid(tmp_path)

    charger = appliance_status(tmp_path)["chargers"][0]

    assert charger.charger_id == "charger-a"
    assert charger.connected is False
    assert charger.connected_at is None


def test_running_server_accepts_connection_after_current_start(tmp_path: Path):
    events = EventStore(tmp_path)
    events.record_runtime("charger_connected", charger_id="charger-a")
    events.record_runtime("server_started")
    events.record_runtime("charger_connected", charger_id="charger-a")
    write_pid(tmp_path)

    charger = appliance_status(tmp_path)["chargers"][0]

    assert charger.connected is True
    assert charger.connected_at is not None


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


def test_status_tracks_latest_negotiated_subprotocol(tmp_path: Path):
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


def test_missing_subprotocol_does_not_reject_charger(tmp_path: Path):
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

    charger = appliance_status(tmp_path)["chargers"][0]

    assert charger.connected is True
    assert charger.subprotocol is None
