from __future__ import annotations

import asyncio
import json
import sqlite3

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.export_contract import export_contract
from ocpp_csms.transactions.archive import TransactionArchive
from ocpp_csms.transactions.changes import changes_page


def test_export_does_not_create_revision_database(tmp_path):
    EventStore(tmp_path)
    assert not (tmp_path / "transaction-changes.sqlite3").exists()
    export_contract(tmp_path)
    assert not (tmp_path / "transaction-changes.sqlite3").exists()


def test_export_does_not_reconcile_archive_corrections(tmp_path):
    EventStore(tmp_path)
    archive = TransactionArchive(tmp_path)
    transaction_id = asyncio.run(archive.start("cp-a", {"connector_id": 1, "timestamp": "2026-01-01T00:00:00Z"}))
    before = changes_page(tmp_path)["items"]
    path = archive._paths[transaction_id]
    record = json.loads(path.read_text())
    record["manual_correction"] = True
    path.write_text(json.dumps(record))
    export_contract(tmp_path)
    assert changes_page(tmp_path)["items"] == before
    TransactionArchive(tmp_path)
    after = changes_page(tmp_path)["items"]
    assert len(after) == len(before) + 1
    assert after[-1]["record"]["manual_correction"] is True


def test_changes_page_is_read_only_and_cursor_is_independent(tmp_path):
    EventStore(tmp_path)
    archive = TransactionArchive(tmp_path)
    transaction_id = asyncio.run(archive.start("cp-a", {"connector_id": 1}))
    first = export_contract(tmp_path, changes_after=0)["data"]["transaction_changes"]
    assert len(first["items"]) == 1
    cursor = first["cursor"]["next"]
    second = export_contract(tmp_path, changes_after=cursor)["data"]["transaction_changes"]
    assert second["items"] == []
    assert second["cursor"]["next"] == cursor
    assert first["items"][0]["transaction_id"] == transaction_id
    connection = sqlite3.connect(tmp_path / "transaction-changes.sqlite3")
    try:
        assert connection.execute("SELECT COUNT(*) FROM changes").fetchone()[0] == 1
    finally:
        connection.close()
