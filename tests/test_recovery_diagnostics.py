import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest

from ocpp_csms.evidence.store import DATABASE_FILENAME, EventStore
from ocpp_csms.session import ChargePointSession
from ocpp_csms.transactions import TransactionArchive


def make_session(tmp_path, charger_id="charger-a"):
    return ChargePointSession(
        charger_id,
        SimpleNamespace(last_frame="test"),
        TransactionArchive(tmp_path),
        EventStore(tmp_path),
    )


def runtime_events(tmp_path):
    with closing(sqlite3.connect(tmp_path / DATABASE_FILENAME)) as connection:
        rows = connection.execute(
            "SELECT event, charger_id, details_json FROM runtime_events ORDER BY id"
        ).fetchall()
    return [
        (event, charger_id, json.loads(details) if details is not None else None)
        for event, charger_id, details in rows
    ]


@pytest.mark.asyncio
async def test_recovery_emits_adopted_and_attached_diagnostics(tmp_path):
    session = make_session(tmp_path)

    await session.on_meter_values(
        transaction_id=225,
        connector_id=1,
        meter_value=[{"timestamp": "2026-10-02T12:05:00Z"}],
    )
    await session.on_stop_transaction(
        transaction_id=225,
        connector_id=1,
        timestamp="2026-10-02T12:10:00Z",
        meter_stop=150,
    )

    events = runtime_events(tmp_path)
    assert [entry[0] for entry in events] == [
        "historical_transaction_recovered",
        "historical_transaction_evidence_attached",
    ]
    recovered = events[0][2]
    assert recovered == {
        "connector_id": 1,
        "decision": "adopted",
        "message_type": "MeterValues",
        "transaction_id": 225,
    }
    attached = events[1][2]
    assert attached == {
        "connector_id": 1,
        "decision": "attached",
        "message_type": "StopTransaction",
        "transaction_id": 225,
    }


@pytest.mark.asyncio
async def test_collision_emits_collision_and_unresolved_diagnostics(tmp_path):
    session = make_session(tmp_path)
    start = await session.on_start_transaction(
        connector_id=1,
        id_tag="card-a",
        meter_start=100,
        timestamp="2026-10-02T12:00:00Z",
    )
    assert start.transaction_id == 1

    await session.on_stop_transaction(
        transaction_id=1,
        connector_id=2,
        meter_stop=150,
        timestamp="2026-10-02T12:10:00Z",
    )

    events = runtime_events(tmp_path)
    assert [entry[0] for entry in events] == [
        "historical_transaction_id_collision",
        "unresolved_queued_message_preserved",
    ]
    collision = events[0][2]
    assert collision["transaction_id"] == 1
    assert collision["connector_id"] == 2
    assert collision["message_type"] == "StopTransaction"
    assert collision["decision"] == "preserved_unresolved"
    assert collision["reason"] == "connector_mismatch"
    assert collision["unresolved_path"].startswith("transactions-unresolved/")
    assert events[1][2] == collision


@pytest.mark.asyncio
async def test_collision_does_not_mutate_sqlite_local_transaction(tmp_path):
    session = make_session(tmp_path)
    start = await session.on_start_transaction(
        connector_id=1,
        id_tag="card-a",
        meter_start=100,
        timestamp="2026-10-02T12:00:00Z",
    )
    assert start.transaction_id == 1

    await session.on_stop_transaction(
        transaction_id=1,
        connector_id=2,
        meter_stop=999,
        timestamp="2026-10-02T12:10:00Z",
    )

    with closing(sqlite3.connect(tmp_path / DATABASE_FILENAME)) as connection:
        row = connection.execute(
            "SELECT state, meter_stop, stopped_at FROM transactions WHERE transaction_id = 1"
        ).fetchone()
    assert row == ("open", None, None)
