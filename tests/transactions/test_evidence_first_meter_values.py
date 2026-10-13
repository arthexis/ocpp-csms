import json
import sqlite3
from types import SimpleNamespace

import pytest
from ocpp.v16 import call_result

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.session import ChargePointSession
from ocpp_csms.transactions.archive import TransactionArchive


def session_for(tmp_path):
    return ChargePointSession(
        "cp-a", SimpleNamespace(last_frame="meter"),
        TransactionArchive(tmp_path), EventStore(tmp_path),
    )


def archived(tmp_path):
    return [json.loads(p.read_text()) for p in (tmp_path / "transactions").glob("*/*.json")]


@pytest.mark.asyncio
async def test_meter_values_without_start_retains_incomplete_archive_and_raw_event(tmp_path):
    session = session_for(tmp_path)
    payload = {
        "connector_id": 1, "transaction_id": 918,
        "meter_value": [{"timestamp": "2026-10-13T12:05:00Z",
                         "sampled_value": [{"value": "1200"}]}],
    }
    result = await session.on_meter_values(**payload)
    assert isinstance(result, call_result.MeterValuesPayload)
    records = archived(tmp_path)
    assert len(records) == 1
    assert records[0]["transaction_id"] == 918
    assert records[0]["start"] is None
    assert records[0]["stop"] is None
    assert records[0]["meter_values"] == [payload]
    with sqlite3.connect(session.events.path) as connection:
        events = connection.execute(
            "SELECT payload_json FROM events WHERE action = 'MeterValues' AND direction = 'in'"
        ).fetchall()
        assert len(events) == 1
        assert json.loads(events[0][0]) == payload
        assert connection.execute(
            "SELECT COUNT(*) FROM transactions WHERE transaction_id = 918"
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_missing_start_meter_values_append_after_archive_restart(tmp_path):
    first = session_for(tmp_path)
    await first.on_meter_values(
        connector_id=1, transaction_id=918,
        meter_value=[{"timestamp": "2026-10-13T12:05:00Z"}],
    )
    second = session_for(tmp_path)
    result = await second.on_meter_values(
        connector_id=1, transaction_id=918,
        meter_value=[{"timestamp": "2026-10-13T12:06:00Z"}],
    )
    assert isinstance(result, call_result.MeterValuesPayload)
    records = archived(tmp_path)
    assert len(records) == 1
    assert records[0]["start"] is None
    assert len(records[0]["meter_values"]) == 2


@pytest.mark.asyncio
async def test_connector_only_meter_values_keeps_raw_event_without_transaction(tmp_path):
    session = session_for(tmp_path)
    payload = {
        "connector_id": 1,
        "meter_value": [{"timestamp": "2026-10-13T12:07:00Z"}],
    }
    result = await session.on_meter_values(**payload)
    assert isinstance(result, call_result.MeterValuesPayload)
    assert archived(tmp_path) == []
    with sqlite3.connect(session.events.path) as connection:
        events = connection.execute(
            "SELECT payload_json FROM events WHERE action = 'MeterValues' AND direction = 'in'"
        ).fetchall()
    assert len(events) == 1
    assert json.loads(events[0][0]) == payload
