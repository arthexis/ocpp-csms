import json
import sqlite3
from types import SimpleNamespace

import pytest

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.session import ChargePointSession
from ocpp_csms.transactions.archive import TransactionArchive


def records(root):
    return [json.loads(p.read_text()) for p in root.glob("*/*.json")]


def session_for(tmp_path, cp="cp-a"):
    return ChargePointSession(
        cp, SimpleNamespace(last_frame="conflicting frame"),
        TransactionArchive(tmp_path), EventStore(tmp_path),
    )


@pytest.mark.asyncio
async def test_conflicting_meter_preserves_raw_and_unresolved_without_touching_local_state(tmp_path):
    session = session_for(tmp_path)
    start = await session.on_start_transaction(
        connector_id=1, id_tag="card", meter_start=100,
        timestamp="2026-10-13T12:00:00Z",
    )
    tx = start.transaction_id
    payload = {
        "connector_id": 2, "transaction_id": tx,
        "meter_value": [{"timestamp": "2026-10-13T12:05:00Z"}],
    }
    with sqlite3.connect(session.events.path) as db:
        before = db.execute(
            "SELECT state, last_activity_at FROM transactions WHERE transaction_id = ?", (tx,)
        ).fetchone()
    await session.on_meter_values(**payload)
    with sqlite3.connect(session.events.path) as db:
        after = db.execute(
            "SELECT state, last_activity_at FROM transactions WHERE transaction_id = ?", (tx,)
        ).fetchone()
        raw = db.execute(
            "SELECT payload_json FROM events WHERE action = 'MeterValues' AND direction = 'in'"
        ).fetchall()
    assert before == after
    assert json.loads(raw[-1][0]) == payload
    original = records(tmp_path / "transactions")
    assert len(original) == 1 and original[0]["meter_values"] == []
    unresolved = records(tmp_path / "transactions-unresolved")
    assert len(unresolved) == 1
    assert unresolved[0]["reason"] == "connector_mismatch"
    assert unresolved[0]["payload"] == payload


@pytest.mark.asyncio
async def test_recovered_meter_connector_conflict_remains_separate_after_restart(tmp_path):
    first = session_for(tmp_path)
    original = {
        "connector_id": 1, "transaction_id": 918,
        "meter_value": [{"timestamp": "2026-10-13T12:05:00Z"}],
    }
    await first.on_meter_values(**original)
    second = session_for(tmp_path)
    conflicting = {**original, "connector_id": 2}
    await second.on_meter_values(**conflicting)
    recovered = records(tmp_path / "transactions")
    assert len(recovered) == 1
    assert recovered[0]["start"] is None
    assert recovered[0]["meter_values"] == [original]
    unresolved = records(tmp_path / "transactions-unresolved")
    assert len(unresolved) == 1
    assert unresolved[0]["payload"] == conflicting
    assert unresolved[0]["reason"] == "connector_mismatch"


@pytest.mark.asyncio
async def test_foreign_cp_stop_does_not_close_original_sqlite_transaction(tmp_path):
    original = session_for(tmp_path)
    start = await original.on_start_transaction(
        connector_id=1, id_tag="card", meter_start=100,
        timestamp="2026-10-13T12:00:00Z",
    )
    tx = start.transaction_id
    foreign = session_for(tmp_path, "cp-b")
    payload = {
        "transaction_id": tx, "meter_stop": 900,
        "timestamp": "2026-10-13T12:10:00Z",
    }
    await foreign.on_stop_transaction(**payload)
    with sqlite3.connect(original.events.path) as db:
        state = db.execute(
            "SELECT charger_id, state, stopped_at FROM transactions WHERE transaction_id = ?", (tx,)
        ).fetchone()
        raw = db.execute(
            "SELECT charger_id, payload_json FROM events WHERE action = 'StopTransaction' AND direction = 'in'"
        ).fetchall()
    assert state == ("cp-a", "open", None)
    assert raw[-1][0] == "cp-b" and json.loads(raw[-1][1]) == payload
    unresolved = records(tmp_path / "transactions-unresolved")
    assert len(unresolved) == 1
    assert unresolved[0]["reason"] == "charge_point_mismatch"
    assert records(tmp_path / "transactions")[0]["stop"] is None
