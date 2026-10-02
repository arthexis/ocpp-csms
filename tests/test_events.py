import json
import sqlite3

import pytest

from ocpp_csms.events import DATABASE_FILENAME, LEGACY_DATABASE_FILENAME, EventStore


def database(tmp_path):
    return tmp_path / DATABASE_FILENAME


def test_event_store_records_ocpp_and_runtime_events(tmp_path):
    store = EventStore(tmp_path)

    store.record_ocpp(
        "charger-a",
        "Authorize",
        {"id_tag": "card-a", "timestamp": "2026-10-01T15:00:00Z"},
    )
    store.record_runtime("charger_connected", charger_id="charger-a")

    connection = sqlite3.connect(database(tmp_path))
    event = connection.execute(
        "SELECT charger_id, action, id_tag, charger_timestamp, payload_json FROM events"
    ).fetchone()
    runtime = connection.execute(
        "SELECT event, charger_id FROM runtime_events"
    ).fetchone()
    version = connection.execute("PRAGMA user_version").fetchone()[0]

    assert event[:4] == (
        "charger-a",
        "Authorize",
        "card-a",
        "2026-10-01T15:00:00Z",
    )
    assert json.loads(event[4])["id_tag"] == "card-a"
    assert runtime == ("charger_connected", "charger-a")
    assert version == 2


def test_event_store_renames_legacy_database(tmp_path):
    legacy = tmp_path / LEGACY_DATABASE_FILENAME
    connection = sqlite3.connect(legacy)
    connection.execute("PRAGMA user_version = 2")
    connection.executescript(
        """
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            received_at TEXT NOT NULL,
            charger_id TEXT NOT NULL,
            action TEXT NOT NULL,
            direction TEXT NOT NULL,
            transaction_id INTEGER,
            id_tag TEXT,
            charger_timestamp TEXT,
            payload_json TEXT NOT NULL
        );
        CREATE TABLE runtime_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            occurred_at TEXT NOT NULL,
            event TEXT NOT NULL,
            charger_id TEXT,
            details_json TEXT
        );
        """
    )
    connection.commit()
    connection.close()

    store = EventStore(tmp_path)

    assert store.path == database(tmp_path)
    assert store.path.exists()
    assert not legacy.exists()


def test_event_store_does_not_wait_for_locked_database(tmp_path):
    store = EventStore(tmp_path)
    lock = sqlite3.connect(database(tmp_path))
    lock.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            store.record_runtime("charger_connected", charger_id="charger-a")
    finally:
        lock.rollback()
        lock.close()


def test_start_retry_returns_existing_transaction(tmp_path):
    store = EventStore(tmp_path)
    payload = {
        "connector_id": 2,
        "id_tag": "2C661746",
        "meter_start": 18462750,
        "timestamp": "2026-10-01T17:28:16Z",
    }

    store.record_transaction_start(227, "charger-a", payload)

    assert store.find_recent_start("charger-a", payload) == 227
    rows = sqlite3.connect(database(tmp_path)).execute(
        "SELECT transaction_id, state FROM transactions"
    ).fetchall()
    assert rows == [(227, "open")]


def test_equal_timestamp_conflict_does_not_replace_available(tmp_path):
    store = EventStore(tmp_path)
    available = {
        "connector_id": 2,
        "status": "Available",
        "error_code": "NoError",
        "timestamp": "2026-10-01T19:05:36Z",
    }
    preparing = {
        **available,
        "status": "Preparing",
    }

    assert store.record_connector_status("charger-a", available) is True
    assert store.record_connector_status("charger-a", preparing) is False

    row = sqlite3.connect(database(tmp_path)).execute(
        "SELECT status, event_timestamp FROM connector_status"
    ).fetchone()
    assert row == ("Available", "2026-10-01T19:05:36Z")


def test_terminal_status_ends_open_transaction_without_fabricating_stop(tmp_path):
    store = EventStore(tmp_path)
    start = {
        "connector_id": 2,
        "id_tag": "2C661746",
        "meter_start": 18462750,
        "timestamp": "2026-10-01T17:28:16Z",
    }
    store.record_transaction_start(227, "charger-a", start)

    store.record_connector_status(
        "charger-a",
        {
            "connector_id": 2,
            "status": "Finishing",
            "error_code": "NoError",
            "timestamp": "2026-10-01T19:05:28Z",
        },
    )

    row = sqlite3.connect(database(tmp_path)).execute(
        "SELECT state, meter_stop, stopped_at FROM transactions WHERE transaction_id = 227"
    ).fetchone()
    assert row == ("ended", None, None)


def test_duplicate_stop_is_idempotent_and_summary_calculates_energy(tmp_path):
    store = EventStore(tmp_path)
    store.record_transaction_start(
        140,
        "charger-a",
        {
            "connector_id": 1,
            "id_tag": "7CCD1F46",
            "meter_start": 21498520,
            "timestamp": "2026-06-18T17:42:54Z",
        },
    )
    stop = {
        "transaction_id": 140,
        "meter_stop": 21499690,
        "timestamp": "2026-06-18T17:44:40Z",
    }

    store.record_transaction_stop("charger-a", stop)
    store.record_transaction_stop("charger-a", stop)

    row = sqlite3.connect(database(tmp_path)).execute(
        "SELECT state, energy_wh FROM transaction_summary WHERE transaction_id = 140"
    ).fetchone()
    assert row == ("stopped", 1170)
