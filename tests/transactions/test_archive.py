"""JSON archive is a best-effort diagnostic projection, not a transaction authority."""
import json

import pytest

from ocpp_csms.transactions.archive import TransactionArchive


START = {"connector_id": 1, "id_tag": "card", "meter_start": 100, "timestamp": "2026-10-03T10:00:00Z"}


def records(root):
    return [json.loads(path.read_text(encoding="utf-8"))
            for path in (root / "transactions").glob("*/*.json")]


@pytest.mark.asyncio
async def test_diagnostic_start_uses_sqlite_assigned_id(tmp_path):
    archive = TransactionArchive(tmp_path)
    await archive.diagnostic_start("charger-a", START, 225)
    assert len(records(tmp_path)) == 1
    assert records(tmp_path)[0]["transaction_id"] == 225
    assert records(tmp_path)[0]["start"] == START


@pytest.mark.asyncio
async def test_diagnostic_start_is_idempotent_across_restart(tmp_path):
    await TransactionArchive(tmp_path).diagnostic_start("charger-a", START, 225)
    await TransactionArchive(tmp_path).diagnostic_start("charger-a", START, 225)
    assert len(records(tmp_path)) == 1


@pytest.mark.asyncio
async def test_unknown_transaction_does_not_create_archive(tmp_path):
    archive = TransactionArchive(tmp_path)
    await archive.meter_values("charger-a", {"transaction_id": 225, "meter_value": []})
    await archive.stop("charger-a", {"transaction_id": 225, "meter_stop": 200})
    assert records(tmp_path) == []


@pytest.mark.asyncio
async def test_matching_evidence_updates_existing_diagnostic(tmp_path):
    archive = TransactionArchive(tmp_path)
    await archive.diagnostic_start("charger-a", START, 225)
    await archive.meter_values("charger-a", {"transaction_id": 225, "connector_id": 1, "meter_value": []})
    await archive.stop("charger-a", {"transaction_id": 225, "meter_stop": 200})
    record = records(tmp_path)[0]
    assert len(record["meter_values"]) == 1
    assert record["stop"]["meter_stop"] == 200


@pytest.mark.asyncio
async def test_conflicting_diagnostic_evidence_does_not_mutate_record(tmp_path):
    archive = TransactionArchive(tmp_path)
    await archive.diagnostic_start("charger-a", START, 225)
    original = records(tmp_path)[0]
    await archive.stop("charger-b", {"transaction_id": 225, "meter_stop": 200})
    assert records(tmp_path)[0] == original
