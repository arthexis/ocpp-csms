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
    transaction_id = events.commit_start("charger-a", start_payload())

    data = appliance_status(tmp_path)
    charger = data["chargers"][0]

    assert data["server"] == "running"
    assert charger.charger_id == "charger-a"
    assert charger.status == "Charging"
    assert charger.transaction_id == transaction_id
    assert charger.id_tag == "card-a"


def test_charging_filter_uses_reported_connector_state(tmp_path: Path):
    events = EventStore(tmp_path)
    for charger in ("charger-a", "charger-b"):
        events.record_runtime("charger_connected", charger_id=charger)
        events.record_connector_status(
            charger,
            {"connector_id": 1, "status": "Charging", "timestamp": "2026-10-01T15:00:00Z"},
        )

    active = events.commit_start("charger-a", start_payload())
    finished = events.commit_start("charger-b", start_payload(timestamp="2026-10-01T14:00:00Z"))
    events.commit_transaction_evidence(
            "charger-b", "StopTransaction",
            {
                "transaction_id": finished,
                "meter_stop": 1200,
                "timestamp": "2026-10-01T14:30:00Z",
            },
        )

    # A real stop does not itself replace the last reported connector status.
    # The charger reports that connector B is available again.
    events.record_connector_status(
        "charger-b",
        {"connector_id": 1, "status": "Available", "timestamp": "2026-10-01T15:01:00Z"},
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


def test_historical_open_does_not_claim_charging_without_connector_evidence(tmp_path: Path):
    events = EventStore(tmp_path)
    transaction_id = events.commit_start("charger-a", start_payload())
    data = appliance_status(tmp_path)
    assert data["active_chargers"] == []
    charger = data["chargers"][0]
    assert charger.status is None
    assert charger.transaction_id == transaction_id


def test_available_status_supersedes_historical_open_for_charging_filter(tmp_path: Path):
    events = EventStore(tmp_path)
    events.commit_start("charger-a", start_payload())
    events.record_connector_status(
        "charger-a",
        {"connector_id": 1, "status": "Available", "timestamp": "2026-10-01T15:02:00Z"},
    )
    data = appliance_status(tmp_path)
    assert data["active_chargers"] == []
    assert data["chargers"][0].status == "Available"
    assert "charger-a" not in format_status(data, charging_only=True)


def test_status_displays_compiled_and_database_schema(tmp_path: Path):
    import sqlite3

    from ocpp_csms.schema import CURRENT_SCHEMA_VERSION
    from ocpp_csms.status_contract import status_contract

    missing = appliance_status(tmp_path)
    assert missing["schema_compiled"] == CURRENT_SCHEMA_VERSION
    assert missing["schema_database"] is None
    assert "Schema (database): -" in format_status(missing)
    assert status_contract(missing)["data"]["storage"]["schema_database"] is None

    EventStore(tmp_path)
    current = appliance_status(tmp_path)
    assert current["schema_database"] == CURRENT_SCHEMA_VERSION
    assert f"Schema (code): {CURRENT_SCHEMA_VERSION}" in format_status(current)
    assert f"Schema (database): {CURRENT_SCHEMA_VERSION}" in format_status(current)

    with sqlite3.connect(tmp_path / "ocpp-csms.sqlite3") as db:
        db.execute("PRAGMA user_version = 6")
    different = appliance_status(tmp_path)
    assert different["schema_compiled"] == CURRENT_SCHEMA_VERSION
    assert different["schema_database"] == 6
    assert "Schema (database): 6" in format_status(different)
    assert status_contract(different)["data"]["storage"]["schema_compiled"] == CURRENT_SCHEMA_VERSION
    assert status_contract(different)["data"]["storage"]["schema_database"] == 6


def test_connected_charger_defaults_to_available_without_contrary_evidence(tmp_path: Path):
    from ocpp_csms.status_contract import status_contract

    events = EventStore(tmp_path)
    events.record_runtime("server_started")
    events.record_runtime("charger_connected", charger_id="charger-a")
    write_pid(tmp_path)

    charger = appliance_status(tmp_path)["chargers"][0]
    assert charger.connected is True
    assert charger.status is None  # No fabricated StatusNotification.
    assert charger.derived_status == "Available"
    assert charger.availability == "available"
    assert charger.activity == "inactive"
    assert "Available" in format_status(appliance_status(tmp_path))
    assert "OCPP status: not reported" in format_status(appliance_status(tmp_path), charger_id="charger-a")
    data = status_contract(appliance_status(tmp_path))["data"]["chargers"][0]
    assert data["status"] is None
    assert data["derived_status"] == "Available"


def test_open_transaction_prevents_assumed_available(tmp_path: Path):
    events = EventStore(tmp_path)
    events.record_runtime("server_started")
    events.record_runtime("charger_connected", charger_id="charger-a")
    events.commit_start("charger-a", start_payload())
    write_pid(tmp_path)

    charger = appliance_status(tmp_path)["chargers"][0]
    assert charger.status is None
    assert charger.derived_status is None


def test_disconnected_charger_is_not_assumed_available(tmp_path: Path):
    events = EventStore(tmp_path)
    events.record_runtime("charger_connected", charger_id="charger-a")
    events.record_runtime("charger_disconnected", charger_id="charger-a")

    charger = appliance_status(tmp_path)["chargers"][0]
    assert not charger.connected
    assert charger.derived_status is None


def test_status_last_seen_omits_microseconds_and_aligns_long_ids():
    from ocpp_csms.status import ChargerStatus, format_status
    charger = ChargerStatus(
        charger_id="GSCSC0824110052X0124", connected=True,
        connected_at=None, subprotocol=None,
        last_seen="2026-10-15T15:44:42.476504Z",
        status="Available", error_code=None,
        transaction_id=None, id_tag=None, started_at=None, connectors=[],
    )
    lines = format_status({"chargers": [charger], "active_chargers": []}).splitlines()
    header = next(line for line in lines if line.startswith("ID "))
    record = next(line for line in lines if line.startswith(charger.charger_id))
    assert header.index("Connected") == record.index("yes")
    assert "2026-10-15T15:44:42Z" in record
    assert ".476504" not in record


def test_timestamp_presentation_preserves_raw_evidence():
    from ocpp_csms.status import display_timestamp
    assert display_timestamp("2026-10-15T15:44:42.476504Z") == "2026-10-15T15:44:42Z"
    assert display_timestamp("2026-10-15T15:44:42.476504+00:00") == "2026-10-15T15:44:42+00:00"
    assert display_timestamp(None) == "-"


def test_connected_duration_formats_seconds_minutes_hours_and_days():
    from datetime import datetime, timedelta, timezone
    from ocpp_csms.status import connected_duration

    now = datetime(2026, 10, 15, 16, 0, tzinfo=timezone.utc)
    def duration(seconds):
        return connected_duration(True, (now - timedelta(seconds=seconds)).isoformat(), now=now)

    assert duration(0) == "0s"
    assert duration(59) == "59s"
    assert duration(60) == "1m"
    assert duration(3599) == "59m"
    assert duration(3600) == "1h"
    assert duration(3660) == "1h 1m"
    assert duration(86400 + 40 * 60) == "1d 40m"
    assert duration(86400 + 3600) == "1d 1h"
    assert connected_duration(False, now.isoformat(), now=now) == "no"
    assert connected_duration(True, None, now=now) == "unknown"


def test_status_table_displays_connection_age_not_boolean():
    from datetime import datetime, timedelta, timezone
    from ocpp_csms.status import ChargerStatus, format_status

    now = datetime.now(timezone.utc)
    charger = ChargerStatus(
        charger_id="charger-a", connected=True,
        connected_at=(now - timedelta(minutes=40)).isoformat(),
        subprotocol="ocpp1.6", last_seen=now.isoformat(),
        status="Available", error_code=None, transaction_id=None,
        id_tag=None, started_at=None, connectors=[],
    )
    line = next(line for line in format_status({"chargers": [charger], "active_chargers": []}).splitlines()
                if line.startswith("charger-a"))
    assert "40m" in line
    assert "yes" not in line
