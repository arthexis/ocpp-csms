import json
from unittest.mock import patch

import pytest

from ocpp_csms.transactions.archive import TransactionArchive


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["MeterValues", "StopTransaction"])
async def test_unreadable_existing_archive_quarantines_incoming_evidence(tmp_path, action):
    archive = TransactionArchive(tmp_path)
    start = {
        "connector_id": 1, "id_tag": "card", "meter_start": 100,
        "timestamp": "2026-10-13T12:00:00Z",
    }
    transaction_id = await archive.start("cp-a", start)
    original_path = archive._paths[transaction_id]
    original_bytes = original_path.read_bytes()
    payload = {"transaction_id": transaction_id, "meter_stop": 180,
               "timestamp": "2026-10-13T12:30:00Z"} if action == "StopTransaction" else {
        "connector_id": 1, "transaction_id": transaction_id,
        "meter_value": [{"timestamp": "2026-10-13T12:05:00Z"}],
    }
    real_read = type(original_path).read_text

    def inaccessible(path, *args, **kwargs):
        if path == original_path:
            raise PermissionError("archive unavailable")
        return real_read(path, *args, **kwargs)

    with patch.object(type(original_path), "read_text", inaccessible):
        if action == "StopTransaction":
            decision = await archive.stop("cp-a", payload)
        else:
            decision = await archive.meter_values("cp-a", payload)
    assert decision["reason"] == "archive_unreadable"
    assert original_path.read_bytes() == original_bytes
    unresolved = list((tmp_path / "transactions-unresolved").glob("*/*.json"))
    assert len(unresolved) == 1
    record = json.loads(unresolved[0].read_text())
    assert record["payload"] == payload
    assert record["reason"] == "archive_unreadable"


@pytest.mark.asyncio
async def test_corrupt_archive_is_not_replaced_by_recovered_evidence(tmp_path):
    archive = TransactionArchive(tmp_path)
    start = {"connector_id": 1, "id_tag": "card", "meter_start": 100,
             "timestamp": "2026-10-13T12:00:00Z"}
    tx = await archive.start("cp-a", start)
    path = archive._paths[tx]
    path.write_text("{broken", encoding="utf-8")
    payload = {"transaction_id": tx, "meter_stop": 180,
               "timestamp": "2026-10-13T12:30:00Z"}
    decision = await archive.stop("cp-a", payload)
    assert decision["reason"] == "archive_unreadable"
    assert path.read_text() == "{broken"
