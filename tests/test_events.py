import json
import sqlite3
from contextlib import closing

import pytest

from ocpp_csms.events import DATABASE_FILENAME, EventStore


def database(tmp_path):
    return tmp_path / DATABASE_FILENAME


def fetchone(tmp_path, sql):
    with closing(sqlite3.connect(database(tmp_path))) as connection:
        return connection.execute(sql).fetchone()


def fetchall(tmp_path, sql):
    with closing(sqlite3.connect(database(tmp_path))) as connection:
        return connection.execute(sql).fetchall()


def test_event_store_records_ocpp_and_runtime_events(tmp_path):
    store = EventStore(tmp_path)

    store.record_ocpp(
        "charger-a",
        "Authorize",
        {"id_tag": "card-a", "timestamp": "2026-10-01T15:00:00Z"},
    )
    store.record_runtime("charger_connected", charger_id="charger-a")

    with closing(sqlite3.connect(database(tmp_path))) as connection:
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
    assert version == 4


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
    assert fetchall(
        tmp_path,
        "SELECT transaction_id, state FROM transactions",
    ) == [(227, "open")]


def test_equal_timestamp_later_receipt_wins(tmp_path):
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
    assert store.record_connector_status("charger-a", preparing) is True

    assert fetchone(
        tmp_path,
        "SELECT status, event_timestamp FROM connector_status",
    ) == ("Preparing", "2026-10-01T19:05:36Z")


def test_older_status_does_not_replace_newer_connector_state(tmp_path):
    store = EventStore(tmp_path)
    assert store.record_connector_status(
        "charger-a",
        {
            "connector_id": 1,
            "status": "Charging",
            "timestamp": "2026-10-01T15:10:00Z",
        },
    ) is True
    assert store.record_connector_status(
        "charger-a",
        {
            "connector_id": 1,
            "status": "Preparing",
            "timestamp": "2026-10-01T15:09:59Z",
        },
    ) is False

    assert fetchone(
        tmp_path,
        "SELECT status FROM connector_status",
    ) == ("Charging",)


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

    assert fetchone(
        tmp_path,
        "SELECT state, meter_stop, stopped_at FROM transactions WHERE transaction_id = 227",
    ) == ("ended", None, None)


def test_delayed_terminal_status_does_not_end_newer_session(tmp_path):
    store = EventStore(tmp_path)
    store.record_transaction_start(
        7,
        "charger-a",
        {
            "connector_id": 1,
            "id_tag": "card-a",
            "meter_start": 100,
            "timestamp": "2026-10-01T15:10:00Z",
        },
    )

    assert store.record_connector_status(
        "charger-a",
        {
            "connector_id": 1,
            "status": "Available",
            "timestamp": "2026-10-01T15:05:00Z",
        },
    ) is True
    assert fetchone(
        tmp_path,
        "SELECT state FROM transactions WHERE transaction_id = 7",
    ) == ("open",)

    assert store.record_connector_status(
        "charger-a",
        {
            "connector_id": 1,
            "status": "Available",
            "timestamp": "2026-10-01T15:11:00Z",
        },
    ) is True
    assert fetchone(
        tmp_path,
        "SELECT state FROM transactions WHERE transaction_id = 7",
    ) == ("ended",)


def test_terminal_status_only_ends_newest_open_transaction(tmp_path):
    store = EventStore(tmp_path)
    for transaction_id, timestamp in (
        (1, "2026-10-01T15:00:00Z"),
        (2, "2026-10-01T15:10:00Z"),
    ):
        store.record_transaction_start(
            transaction_id,
            "charger-a",
            {
                "connector_id": 1,
                "id_tag": "card-a",
                "meter_start": transaction_id * 100,
                "timestamp": timestamp,
            },
        )

    store.record_connector_status(
        "charger-a",
        {
            "connector_id": 1,
            "status": "Available",
            "timestamp": "2026-10-01T15:11:00Z",
        },
    )

    assert fetchall(
        tmp_path,
        "SELECT transaction_id, state FROM transactions ORDER BY transaction_id",
    ) == [(1, "open"), (2, "ended")]


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

    assert fetchone(
        tmp_path,
        "SELECT state, energy_wh FROM transaction_summary WHERE transaction_id = 140",
    ) == ("stopped", 1170)
