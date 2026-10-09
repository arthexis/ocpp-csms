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
        db.execute("PRAGMA user_version=4")
    assert schema_action(tmp_path) == "upgrade"
    with pytest.raises(RuntimeError, match="schema verification failed"):
        verify_schema_integrity(tmp_path)
    assert apply_schema_action(tmp_path) == "upgrade"
    verify_schema_integrity(tmp_path)
    # An already upgraded database must be idempotent.
    assert apply_schema_action(tmp_path) == "current"


def test_schema_verification_rejects_missing_recovery_table(tmp_path):
    create_current_schema(tmp_path)
    with sqlite3.connect(database_path(tmp_path)) as db:
        db.execute("DROP TABLE transaction_recoveries")
    with pytest.raises(RuntimeError, match="recovery audit table missing"):
        verify_schema_integrity(tmp_path)


def test_schema_verification_rejects_missing_db(tmp_path):
    with pytest.raises(RuntimeError, match="schema verification failed"):
        verify_schema_integrity(tmp_path)
