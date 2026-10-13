from __future__ import annotations

import asyncio
import json

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.export_contract import export_contract
from ocpp_csms.transactions.archive import TransactionArchive
from ocpp_csms.transactions.changes import changes_page
from ocpp_csms.transactions.export_metadata import transaction_export_metadata


def test_timestamp_provenance_and_quality_are_nullable_and_utc():
    record = {
        "status": "stopped", "origin": "local",
        "start": {"timestamp": "2026-01-01T01:00:00+01:00", "meter_start": 100},
        "stop": {"timestamp": "invalid", "meter_stop": 300},
        "start_received_at": "2026-01-01T00:01:00Z",
        "stop_received_at": "2026-01-01T00:20:00Z",
        "meter_values": [{"meter_value": [{"timestamp": "2026-01-01T00:10:00Z"}]}],
        "meter_values_received_at": ["2026-01-01T00:11:00Z"],
    }
    result = transaction_export_metadata(record)
    assert result["start_event_at"] == "2026-01-01T00:00:00Z"
    assert result["stop_event_at"] is None
    assert result["start_received_at"] == "2026-01-01T00:01:00Z"
    assert result["meter_sample_times"][0]["received_at"] == "2026-01-01T00:11:00Z"
    assert result["quality"]["missing_meter"] is False
    assert result["quality"]["incomplete"] is False


def test_export_change_feed_contains_quality_and_provenance(tmp_path):
    EventStore(tmp_path)
    archive = TransactionArchive(tmp_path)
    asyncio.run(archive.start("cp-a", {"timestamp": "2026-01-01T01:00:00+01:00", "meter_start": 1}))
    page = export_contract(tmp_path)["data"]
    item = page["transaction_changes"]["items"][0]
    assert item["metadata"]["start_event_at"] == "2026-01-01T00:00:00Z"
    assert item["metadata"]["quality"]["active"] is True
    assert item["metadata"]["quality"]["missing_meter"] is True


def test_journal_survives_restart_and_archive_correction(tmp_path):
    EventStore(tmp_path)
    archive = TransactionArchive(tmp_path)
    transaction_id = asyncio.run(archive.start("cp-a", {"meter_start": 100}))
    before = changes_page(tmp_path)
    path = archive._paths[transaction_id]
    record = json.loads(path.read_text())
    record["correction_note"] = "archive-only"
    path.write_text(json.dumps(record))
    TransactionArchive(tmp_path)
    page = changes_page(tmp_path, after=before["cursor"]["next"])
    assert len(page["items"]) == 1
    assert page["items"][0]["record"]["correction_note"] == "archive-only"
    assert changes_page(tmp_path, after=before["cursor"]["next"])["items"][0]["change_id"] == page["items"][0]["change_id"]
