from __future__ import annotations

import asyncio
import json
import sqlite3

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.schema import database_path
from ocpp_csms.transactions.archive import TransactionArchive
from ocpp_csms.transactions.changes import reconcile_transaction_changes


def _revisions(root):
    with sqlite3.connect(database_path(root)) as connection:
        return connection.execute(
            "SELECT change_id, transaction_id, content_sha256, record_json "
            "FROM transaction_changes ORDER BY change_id"
        ).fetchall()


def test_start_meter_stop_produce_immutable_ordered_revisions(tmp_path):
    EventStore(tmp_path)
    archive = TransactionArchive(tmp_path)
    start = {"connector_id": 1, "id_tag": "CARD", "meter_start": 100, "timestamp": "2026-10-13T12:00:00Z"}
    transaction_id = asyncio.run(archive.start("cp-a", start))
    first = _revisions(tmp_path)
    assert len(first) == 1
    assert json.loads(first[0][3])["status"] == "open"

    asyncio.run(archive.meter_values("cp-a", {
        "transaction_id": transaction_id, "connector_id": 1, "meter_value": []
    }))
    asyncio.run(archive.stop("cp-a", {
        "transaction_id": transaction_id, "meter_stop": 350, "timestamp": "2026-10-13T12:30:00Z"
    }))
    revisions = _revisions(tmp_path)
    assert len(revisions) == 3
    assert [row[0] for row in revisions] == sorted(row[0] for row in revisions)
    assert json.loads(revisions[0][3])["status"] == "open"
    assert json.loads(revisions[-1][3])["status"] == "stopped"
    assert reconcile_transaction_changes(tmp_path) == 0


def test_archive_only_correction_and_repeated_reconciliation(tmp_path):
    EventStore(tmp_path)
    archive = TransactionArchive(tmp_path)
    transaction_id = asyncio.run(archive.start("cp-a", {
        "connector_id": 1, "id_tag": "CARD", "meter_start": 100, "timestamp": "2026-10-13T12:00:00Z"
    }))
    [path] = (tmp_path / "transactions").glob("*/*.json")
    record = json.loads(path.read_text())
    record["id_tag"] = "CORRECTED"
    path.write_text(json.dumps(record))
    assert reconcile_transaction_changes(tmp_path) == 1
    assert reconcile_transaction_changes(tmp_path) == 0
    assert len(_revisions(tmp_path)) == 2
    assert json.loads(_revisions(tmp_path)[-1][3])["id_tag"] == "CORRECTED"
    assert _revisions(tmp_path)[-1][1] == transaction_id


def test_restart_reconciles_archive_change_without_new_event(tmp_path):
    EventStore(tmp_path)
    archive = TransactionArchive(tmp_path)
    asyncio.run(archive.start("cp-a", {
        "connector_id": 1, "id_tag": "CARD", "meter_start": 100, "timestamp": "2026-10-13T12:00:00Z"
    }))
    [path] = (tmp_path / "transactions").glob("*/*.json")
    record = json.loads(path.read_text())
    record["status"] = "stopped"
    path.write_text(json.dumps(record))
    TransactionArchive(tmp_path)
    assert len(_revisions(tmp_path)) == 2


def test_unresolved_evidence_not_journaled(tmp_path):
    EventStore(tmp_path)
    archive = TransactionArchive(tmp_path)
    folder = tmp_path / "transactions-unresolved" / "2026-10-13"
    folder.mkdir(parents=True)
    (folder / "collision.json").write_text(json.dumps({
        "transaction_id": 999, "charge_point_id": "cp-a", "reason": "collision"
    }))
    assert reconcile_transaction_changes(tmp_path) == 0
    assert _revisions(tmp_path) == []
