from __future__ import annotations

import sqlite3

from ocpp_csms.schema import (
    CURRENT_SCHEMA_VERSION,
    create_current_schema,
    database_path,
    inspect_schema,
    upgrade_schema,
)


def test_fresh_schema_has_transaction_change_journal(tmp_path):
    create_current_schema(tmp_path)
    assert inspect_schema(tmp_path).version == CURRENT_SCHEMA_VERSION
    with sqlite3.connect(database_path(tmp_path)) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(transaction_changes)")}
    assert {"change_id", "transaction_id", "charger_id", "changed_at", "content_sha256", "record_json"} <= columns


def test_upgrade_from_v6_preserves_source_and_adds_journal(tmp_path):
    create_current_schema(tmp_path)
    with sqlite3.connect(database_path(tmp_path)) as connection:
        source_before = connection.execute(
            "SELECT value FROM appliance_metadata WHERE key = 'source_id'"
        ).fetchone()[0]
        connection.execute("DROP TABLE transaction_changes")
        connection.execute("PRAGMA user_version = 6")
    upgrade_schema(tmp_path)
    with sqlite3.connect(database_path(tmp_path)) as connection:
        source_after = connection.execute(
            "SELECT value FROM appliance_metadata WHERE key = 'source_id'"
        ).fetchone()[0]
        journal_exists = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='transaction_changes'"
        ).fetchone()
    assert source_after == source_before
    assert journal_exists is not None
    assert inspect_schema(tmp_path).version == CURRENT_SCHEMA_VERSION
