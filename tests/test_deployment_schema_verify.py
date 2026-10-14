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
    assert schema_action(tmp_path) == "upgrade"
    with pytest.raises(RuntimeError, match="schema verification failed"):
        verify_schema_integrity(tmp_path)
    assert apply_schema_action(tmp_path) == "upgrade"
    verify_schema_integrity(tmp_path)
    # An already upgraded database must be idempotent.
    assert apply_schema_action(tmp_path) == "current"


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
