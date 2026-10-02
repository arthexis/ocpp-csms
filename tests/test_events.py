import json
import sqlite3

import pytest

from ocpp_csms.events import EventStore


def test_event_store_records_ocpp_and_runtime_events(tmp_path):
    store = EventStore(tmp_path)

    store.record_ocpp(
        "charger-a",
        "Authorize",
        {"id_tag": "card-a", "timestamp": "2026-10-01T15:00:00Z"},
    )
    store.record_runtime("charger_connected", charger_id="charger-a")

    connection = sqlite3.connect(tmp_path / "events.sqlite3")
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
    assert version == 1


def test_event_store_does_not_wait_for_locked_database(tmp_path):
    store = EventStore(tmp_path)
    lock = sqlite3.connect(tmp_path / "events.sqlite3")
    lock.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            store.record_runtime("charger_connected", charger_id="charger-a")
    finally:
        lock.rollback()
        lock.close()
