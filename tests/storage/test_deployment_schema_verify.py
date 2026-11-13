"""Deployment schema verification must fail closed before service activation."""
import sqlite3

import pytest

from ocpp_csms.install_cutover import apply_schema_action, schema_action, verify_schema_integrity
from ocpp_csms.schema import create_current_schema, database_path


def test_schema_verification_current_database(tmp_path):
    create_current_schema(tmp_path)
    assert schema_action(tmp_path) == "current"
    verify_schema_integrity(tmp_path)


def test_schema_verification_rejects_older_database(tmp_path):
    create_current_schema(tmp_path)
    with sqlite3.connect(database_path(tmp_path)) as db:
        db.execute("ALTER TABLE connector_status DROP COLUMN info")
        db.execute("PRAGMA user_version=5")
    with pytest.raises(RuntimeError, match="older than required"):
        schema_action(tmp_path)
    with pytest.raises(RuntimeError, match="schema verification failed"):
        verify_schema_integrity(tmp_path)
    with pytest.raises(RuntimeError, match="older than required"):
        apply_schema_action(tmp_path)


def test_schema_verification_rejects_missing_db(tmp_path):
    with pytest.raises(RuntimeError, match="schema verification failed"):
        verify_schema_integrity(tmp_path)

def test_wal_conversion_fails_closed_while_database_is_locked(tmp_path):
    """An existing rollback-journal database cannot be switched under a live lock."""
    from ocpp_csms.schema import ensure_wal

    create_current_schema(tmp_path)
    database = database_path(tmp_path)
    with sqlite3.connect(database) as db:
        assert db.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete"

    blocker = sqlite3.connect(database)
    try:
        blocker.execute("BEGIN IMMEDIATE")
        with pytest.raises(RuntimeError, match="WAL readiness failed"):
            ensure_wal(tmp_path)
        assert blocker.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        blocker.rollback()
        blocker.close()

    assert apply_schema_action(tmp_path) == "current"
    verify_schema_integrity(tmp_path)
    with sqlite3.connect(database) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_schema_verification_does_not_convert_rollback_journal(tmp_path):
    create_current_schema(tmp_path)
    database = database_path(tmp_path)
    with sqlite3.connect(database) as db:
        assert db.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete"
    with pytest.raises(RuntimeError, match="WAL verification failed"):
        verify_schema_integrity(tmp_path)
    with sqlite3.connect(database) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"



def test_existing_database_cutover_preserves_export_cursor_and_transaction_identity(tmp_path):
    """A deployed rollback-journal database retains evidence across WAL cutover and restart."""
    from ocpp_csms.evidence.store import EventStore
    from ocpp_csms.export_contract import export_contract
    from ocpp_csms.schema import source_id

    # Build the pre-upgrade state with a closed SQLite writer, not EventStore:
    # EventStore initializes WAL and may leave live connections behind.
    import json

    create_current_schema(tmp_path)
    database = database_path(tmp_path)
    with sqlite3.connect(database) as db:
        assert db.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete"
        transaction_id = 1
        started = "2026-10-03T10:00:00Z"
        payload = {
            "connector_id": 1, "id_tag": "card-a", "meter_start": 100,
            "timestamp": started,
        }
        db.execute(
            """INSERT INTO transactions
               (transaction_id, charger_id, connector_id, id_tag, meter_start,
                started_at, start_received_at, state, last_activity_at)
               VALUES (?, 'charger-a', 1, 'card-a', 100, ?, ?, 'open', ?)""",
            (transaction_id, started, started, started),
        )
        db.execute(
            """INSERT INTO events
               (received_at, charger_id, action, direction, transaction_id,
                id_tag, charger_timestamp, payload_json)
               VALUES (?, 'charger-a', 'StartTransaction', 'in', ?, 'card-a', ?, ?)""",
            (started, transaction_id, started, json.dumps(payload)),
        )
        db.execute("UPDATE transaction_allocator SET last_id = ?", (transaction_id,))

    before = export_contract(tmp_path, after=0, limit=100)["data"]
    original_source = source_id(tmp_path)
    original_ids = [event["id"] for event in before["events"]]
    original_cursor = before["cursor"]["next"]

    assert schema_action(tmp_path) == "current"
    assert apply_schema_action(tmp_path) == "current"
    verify_schema_integrity(tmp_path)
    restarted = EventStore(tmp_path)
    assert source_id(tmp_path) == original_source
    replay = export_contract(tmp_path, after=0, limit=100)["data"]
    assert replay["events"] == before["events"]
    assert replay["cursor"] == before["cursor"]
    assert [event["id"] for event in replay["events"]] == original_ids

    restarted.commit_transaction_evidence("charger-a", "StopTransaction", {
        "transaction_id": transaction_id, "meter_stop": 150,
        "timestamp": "2026-10-03T10:10:00Z",
    })
    later = export_contract(tmp_path, after=original_cursor, limit=100)["data"]
    assert [event["action"] for event in later["events"]] == ["StopTransaction"]
    assert later["events"][0]["transaction_id"] == transaction_id
    assert later["events"][0]["id"] > original_cursor
    assert later["transactions"][0]["id"] == transaction_id
    assert later["transactions"][0]["state"] == "stopped"
