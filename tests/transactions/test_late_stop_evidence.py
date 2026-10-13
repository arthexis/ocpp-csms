import json
import sqlite3
from types import SimpleNamespace

import pytest

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.session import ChargePointSession
from ocpp_csms.transactions.archive import TransactionArchive


START = {
    "connector_id": 1, "id_tag": "card", "meter_start": 100,
    "timestamp": "2026-10-13T12:00:00Z",
}


def session_for(tmp_path):
    return ChargePointSession(
        "cp-a", SimpleNamespace(last_frame="late stop"),
        TransactionArchive(tmp_path), EventStore(tmp_path),
    )


def read_records(tmp_path, directory):
    return [json.loads(p.read_text()) for p in (tmp_path / directory).glob("*/*.json")]


@pytest.mark.asyncio
async def test_genuine_late_stop_completes_only_its_original_transaction(tmp_path):
    session = session_for(tmp_path)
    old = await session.on_start_transaction(**START)
    newer = await session.on_start_transaction(
        **{**START, "meter_start": 200, "timestamp": "2026-10-13T13:00:00Z"}
    )
    stop = {
        "transaction_id": old.transaction_id, "meter_stop": 180,
        "timestamp": "2026-10-13T12:30:00Z",
    }
    await session.on_stop_transaction(**stop)
    records = {r["transaction_id"]: r for r in read_records(tmp_path, "transactions")}
    assert records[old.transaction_id]["stop"] == stop
    assert records[old.transaction_id]["status"] == "stopped"
    assert records[newer.transaction_id]["stop"] is None
    assert records[newer.transaction_id]["status"] == "open"
    with sqlite3.connect(session.events.path) as db:
        states = dict(db.execute("SELECT transaction_id, state FROM transactions"))
    assert states[old.transaction_id] == "stopped"
    assert states[newer.transaction_id] == "open"
    assert read_records(tmp_path, "transactions-unresolved") == []


@pytest.mark.asyncio
async def test_identical_late_stop_retry_is_idempotent_and_conflicting_stop_is_unresolved(tmp_path):
    session = session_for(tmp_path)
    started = await session.on_start_transaction(**START)
    stop = {
        "transaction_id": started.transaction_id, "meter_stop": 180,
        "timestamp": "2026-10-13T12:30:00Z",
    }
    await session.on_stop_transaction(**stop)
    original = read_records(tmp_path, "transactions")[0]
    await session.on_stop_transaction(**stop)
    assert read_records(tmp_path, "transactions") == [original]
    assert read_records(tmp_path, "transactions-unresolved") == []
    conflicting = {**stop, "meter_stop": 999}
    await session.on_stop_transaction(**conflicting)
    assert read_records(tmp_path, "transactions") == [original]
    unresolved = read_records(tmp_path, "transactions-unresolved")
    assert len(unresolved) == 1
    assert unresolved[0]["reason"] == "conflicting_stop"
    assert unresolved[0]["payload"] == conflicting
    with sqlite3.connect(session.events.path) as db:
        row = db.execute(
            "SELECT meter_stop, state FROM transactions WHERE transaction_id = ?",
            (started.transaction_id,),
        ).fetchone()
        raw_count = db.execute(
            "SELECT COUNT(*) FROM events WHERE action = 'StopTransaction' AND direction = 'in'"
        ).fetchone()[0]
    assert row == (180, "stopped")
    assert raw_count == 3


@pytest.mark.asyncio
async def test_late_stop_after_restart_closes_incomplete_without_inference(tmp_path):
    first = session_for(tmp_path)
    started = await first.on_start_transaction(**START)
    second = session_for(tmp_path)
    stop = {
        "transaction_id": started.transaction_id, "meter_stop": 180,
        "timestamp": "2026-10-13T12:30:00Z",
    }
    await second.on_stop_transaction(**stop)
    record = read_records(tmp_path, "transactions")[0]
    assert record["start"] == START
    assert record["stop"] == stop
    assert record["status"] == "stopped"
    assert record.get("recovery") is None
