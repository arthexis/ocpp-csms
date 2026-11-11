import json
import sqlite3
from contextlib import closing

import pytest

from ocpp_csms.evidence.store import DATABASE_FILENAME, EventStore
from ocpp_csms.schema import CURRENT_SCHEMA_VERSION


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
    assert version == CURRENT_SCHEMA_VERSION


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

    transaction_id = store.commit_start("charger-a", payload)
    assert store.commit_start("charger-a", payload) == transaction_id
    assert fetchall(
        tmp_path,
        "SELECT transaction_id, state FROM transactions",
    ) == [(transaction_id, "open")]


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


def test_terminal_status_does_not_mutate_transaction_history(tmp_path):
    store = EventStore(tmp_path)
    start = {
        "connector_id": 2,
        "id_tag": "2C661746",
        "meter_start": 18462750,
        "timestamp": "2026-10-01T17:28:16Z",
    }
    transaction_id = store.commit_start("charger-a", start)

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
        f"SELECT state, meter_stop, stopped_at FROM transactions WHERE transaction_id = {transaction_id}",
    ) == ("open", None, None)


def test_status_notifications_never_close_transaction_history(tmp_path):
    store = EventStore(tmp_path)
    transaction_id = store.commit_start(
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
        f"SELECT state FROM transactions WHERE transaction_id = {transaction_id}",
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
        f"SELECT state FROM transactions WHERE transaction_id = {transaction_id}",
    ) == ("open",)


def test_terminal_status_does_not_end_any_historical_transaction(tmp_path):
    store = EventStore(tmp_path)
    allocated_ids = []
    for transaction_id, timestamp in (
        (1, "2026-10-01T15:00:00Z"),
        (2, "2026-10-01T15:10:00Z"),
    ):
        allocated_id = store.commit_start(
            "charger-a",
            {
                "connector_id": 1,
                "id_tag": "card-a",
                "meter_start": transaction_id * 100,
                "timestamp": timestamp,
            },
        )
        allocated_ids.append(allocated_id)

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
    ) == [(transaction_id, "open") for transaction_id in allocated_ids]


def test_duplicate_stop_is_idempotent_and_summary_calculates_energy(tmp_path):
    store = EventStore(tmp_path)
    transaction_id = store.commit_start(
        "charger-a",
        {
            "connector_id": 1,
            "id_tag": "7CCD1F46",
            "meter_start": 21498520,
            "timestamp": "2026-06-18T17:42:54Z",
        },
    )
    stop = {
        "transaction_id": transaction_id,
        "meter_stop": 21499690,
        "timestamp": "2026-06-18T17:44:40Z",
    }

    store.record_transaction_stop("charger-a", stop)
    store.record_transaction_stop("charger-a", stop)

    assert fetchone(
        tmp_path,
        f"SELECT state, energy_wh FROM transaction_summary WHERE transaction_id = {transaction_id}",
    ) == ("stopped", 1170)


def export_events(tmp_path, after_id=0, limit=100):
    """Test-side model of the durable event cursor; not a consumer API."""
    with closing(sqlite3.connect(database(tmp_path))) as connection:
        return connection.execute(
            "SELECT id, action, transaction_id, payload_json FROM events "
            "WHERE id > ? ORDER BY id LIMIT ?",
            (after_id, limit),
        ).fetchall()


def test_a5_retransmissions_have_distinct_durable_event_ids(tmp_path):
    payload = {"connector_id": 1, "id_tag": "card-a",
               "meter_start": 100, "timestamp": "2026-10-03T10:00:00Z"}
    store = EventStore(tmp_path)
    store.record_ocpp("charger-a", "StartTransaction", payload, transaction_id=41)
    store.record_ocpp("charger-a", "StartTransaction", payload, transaction_id=41)
    rows = export_events(tmp_path)
    assert len(rows) == 2
    assert rows[0][0] < rows[1][0]
    assert [row[2] for row in rows] == [41, 41]
    assert [json.loads(row[3]) for row in rows] == [payload, payload]
    assert export_events(tmp_path, after_id=rows[0][0]) == rows[1:]


def test_a5_cursor_pages_replay_identically_after_restart(tmp_path):
    store = EventStore(tmp_path)
    for n in range(7):
        store.record_ocpp("charger-a", "MeterValues", {
            "transaction_id": 41, "sequence": n,
        })
    expected = export_events(tmp_path)
    cursor = 0
    pages = []
    while True:
        page = export_events(tmp_path, after_id=cursor, limit=2)
        if not page:
            break
        assert all(row[0] > cursor for row in page)
        pages.extend(page)
        cursor = page[-1][0]
    assert pages == expected
    EventStore(tmp_path)
    assert export_events(tmp_path) == expected
    assert export_events(tmp_path, after_id=expected[2][0]) == expected[3:]


def test_a5_late_stop_is_new_event_not_rewrite(tmp_path):
    store = EventStore(tmp_path)
    start = {"connector_id": 1, "id_tag": "card-a",
             "meter_start": 100, "timestamp": "2026-10-03T10:00:00Z"}
    store.record_ocpp("charger-a", "StartTransaction", start, transaction_id=41)
    previous = export_events(tmp_path)
    stop = {"transaction_id": 41, "meter_stop": 150,
            "timestamp": "2026-10-03T10:10:00Z"}
    store.record_ocpp("charger-a", "StopTransaction", stop)
    assert export_events(tmp_path)[:1] == previous
    late = export_events(tmp_path, after_id=previous[-1][0])
    assert len(late) == 1
    assert late[0][1:3] == ("StopTransaction", 41)
    assert json.loads(late[0][3]) == stop


def test_a5_failed_event_insert_never_advances_durable_cursor(tmp_path, monkeypatch):
    store = EventStore(tmp_path)
    store.record_ocpp("charger-a", "Heartbeat", {})
    before = export_events(tmp_path)
    original_connect = store._connect

    def fail_connect():
        connection = original_connect()
        connection.execute("BEGIN EXCLUSIVE")
        connection.close()
        raise sqlite3.OperationalError("simulated write failure")

    monkeypatch.setattr(store, "_connect", fail_connect)
    with pytest.raises(sqlite3.OperationalError, match="simulated write failure"):
        store.record_ocpp("charger-a", "MeterValues", {"transaction_id": 41})
    assert export_events(tmp_path) == before
    monkeypatch.setattr(store, "_connect", original_connect)
    store.record_ocpp("charger-a", "MeterValues", {"transaction_id": 41})
    assert len(export_events(tmp_path, after_id=before[-1][0])) == 1


def test_incoming_stop_does_not_advance_allocator_or_create_transaction(tmp_path):
    store = EventStore(tmp_path)
    store.commit_transaction_evidence("charger-a", "StopTransaction", {
        "transaction_id": 700, "meter_stop": 125, "timestamp": "2026-10-14T12:00:00Z",
    })
    assert fetchone(tmp_path, "SELECT last_id FROM transaction_allocator WHERE singleton = 1") == (0,)
    assert fetchall(
        tmp_path, "SELECT transaction_id, state, meter_start, meter_stop FROM transactions"
    ) == []
    assert store.commit_start("charger-a", {
        "connector_id": 1, "id_tag": "card-a", "meter_start": 125,
        "timestamp": "2026-10-14T12:01:00Z",
    }) == 1


def test_incoming_meter_values_do_not_advance_allocator_across_restart(tmp_path):
    store = EventStore(tmp_path)
    store.commit_transaction_evidence("charger-a", "MeterValues", {"transaction_id": 250})
    store.commit_transaction_evidence("charger-a", "MeterValues", {"transaction_id": 17})
    store.commit_transaction_evidence("charger-a", "MeterValues", {"transaction_id": 250})
    assert fetchone(tmp_path, "SELECT last_id FROM transaction_allocator WHERE singleton = 1") == (0,)
    assert EventStore(tmp_path).commit_start("charger-a", {
        "connector_id": 1, "id_tag": "card-a", "meter_start": 1,
        "timestamp": "2026-10-14T12:02:00Z",
    }) == 1


def test_invalid_external_ids_do_not_advance_allocator(tmp_path):
    store = EventStore(tmp_path)
    for transaction_id in (0, -5, 2147483648):
        store.commit_transaction_evidence("charger-a", "MeterValues", {
            "transaction_id": transaction_id,
        })
    assert fetchone(tmp_path, "SELECT last_id FROM transaction_allocator WHERE singleton = 1") == (0,)


def test_unknown_meter_values_preserve_evidence_without_creating_transaction(tmp_path):
    store = EventStore(tmp_path)
    store.commit_transaction_evidence("charger-a", "MeterValues", {"transaction_id": 420})
    assert fetchall(
        tmp_path,
        "SELECT transaction_id, charger_id, state, started_at, start_received_at "
        "FROM transactions",
    ) == []
    store.commit_transaction_evidence("charger-a", "StopTransaction", {
        "transaction_id": 420, "meter_stop": 90, "timestamp": "2026-10-14T12:10:00Z",
    })
    assert fetchone(
        tmp_path, "SELECT state, meter_stop FROM transactions WHERE transaction_id = 420",
    ) is None


def test_unknown_stop_from_other_charger_does_not_mutate_existing_transaction(tmp_path):
    store = EventStore(tmp_path)
    transaction_id = store.commit_start("charger-a", {
        "connector_id": 1, "id_tag": "a", "meter_start": 10,
        "timestamp": "2026-10-14T12:00:00Z",
    })
    store.commit_transaction_evidence("charger-b", "StopTransaction", {
        "transaction_id": transaction_id, "meter_stop": 99,
        "timestamp": "2026-10-14T12:11:00Z",
    })
    assert fetchone(
        tmp_path, "SELECT charger_id, state, meter_stop FROM transactions WHERE transaction_id = 1",
    ) == ("charger-a", "open", None)
    assert fetchone(tmp_path, "SELECT COUNT(*) FROM events WHERE charger_id = 'charger-b'") == (1,)


def test_session_tables_upgrade_v8_idempotently_preserve_history(tmp_path):
    from ocpp_csms.schema import create_current_schema
    create_current_schema(tmp_path)
    with sqlite3.connect(database(tmp_path)) as connection:
        connection.execute(
            """INSERT INTO events (received_at, charger_id, action, direction, payload_json)
               VALUES ('2026-10-01T00:00:00Z', 'CP-1', 'Heartbeat', 'in', '{}')"""
        )
    first = EventStore(tmp_path)
    first.record_session("session-a", "CP-1", "192.0.2.10", 41234)
    first.record_ocpp("CP-1", "Heartbeat", {}, session_id="session-a")
    EventStore(tmp_path)
    with sqlite3.connect(database(tmp_path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == CURRENT_SCHEMA_VERSION
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM discovery_sessions").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM discovery_event_sessions").fetchone()[0] == 1
        assert connection.execute(
            "SELECT session_id FROM discovery_event_sessions WHERE event_id = 1"
        ).fetchone() is None


def test_session_link_covers_atomic_start_meter_stop_and_retry(tmp_path):
    store = EventStore(tmp_path)
    store.record_session("session-a", "CP-1", "192.0.2.10", 41234)
    start = {
        "connector_id": 1, "id_tag": "card-a", "meter_start": 100,
        "timestamp": "2026-10-01T15:00:00Z",
    }
    transaction_id = store.commit_start("CP-1", start, session_id="session-a")
    assert store.commit_start("CP-1", start, session_id="session-a") == transaction_id
    store.commit_transaction_evidence(
        "CP-1", "MeterValues", {"transaction_id": transaction_id}, session_id="session-a"
    )
    store.commit_transaction_evidence(
        "CP-1", "StopTransaction",
        {"transaction_id": transaction_id, "meter_stop": 150, "timestamp": "2026-10-01T15:10:00Z"},
        session_id="session-a",
    )
    with sqlite3.connect(database(tmp_path)) as connection:
        rows = connection.execute(
            """SELECT e.action, s.session_id FROM events e
               JOIN discovery_event_sessions s ON s.event_id = e.id
               ORDER BY e.id"""
        ).fetchall()
        assert rows == [
            ("StartTransaction", "session-a"), ("StartTransaction", "session-a"),
            ("MeterValues", "session-a"), ("StopTransaction", "session-a"),
        ]


def test_event_store_closes_connections_after_success(tmp_path, monkeypatch):
    """The transaction context commits but does not close sqlite3 connections."""
    store = EventStore(tmp_path)
    original_connect = store._connect
    opened = []

    def tracked_connect():
        connection = original_connect()
        opened.append(connection)
        return connection

    monkeypatch.setattr(store, "_connect", tracked_connect)
    store._ensure_session_evidence()
    store.record_session("session-a", "charger-a", "192.0.2.5", 40000)
    store.record_ocpp("charger-a", "Heartbeat", {}, session_id="session-a")
    start = {
        "connector_id": 1,
        "id_tag": "card-a",
        "meter_start": 100,
        "timestamp": "2026-10-01T15:10:00Z",
    }
    transaction_id = store.commit_start("charger-a", start, session_id="session-a")
    store.commit_transaction_evidence(
        "charger-a", "MeterValues",
        {"transaction_id": transaction_id, "connector_id": 1, "meter_value": []},
        session_id="session-a",
    )
    assert store.record_connector_status(
        "charger-a",
        {"connector_id": 1, "status": "Available", "timestamp": "2026-10-01T15:11:00Z"},
    )
    assert store.latest_rfid_list_version("charger-a") is None
    store.record_rfid_list(
        "charger-a", list_version=1, entries=[], source_file=None,
        list_hash="fixture", verified_version=None,
    )
    store.record_runtime("charger_connected", charger_id="charger-a")

    assert len(opened) == 9
    for connection in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def test_event_store_closes_connection_and_rolls_back_failed_start(tmp_path, monkeypatch):
    store = EventStore(tmp_path)
    original_connect = store._connect
    opened = []

    def tracked_connect():
        connection = original_connect()
        opened.append(connection)
        return connection

    monkeypatch.setattr(store, "_connect", tracked_connect)
    # Validation fails after BEGIN IMMEDIATE and allocator update.
    with pytest.raises(KeyError, match="timestamp"):
        store.commit_start(
            "charger-a", {"connector_id": 1, "id_tag": "card-a", "meter_start": 100}
        )

    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].execute("SELECT 1")
    assert fetchone(tmp_path, "SELECT last_id FROM transaction_allocator") == (0,)
    assert fetchone(tmp_path, "SELECT COUNT(*) FROM transactions") == (0,)
    assert fetchone(tmp_path, "SELECT COUNT(*) FROM events") == (0,)
