"""Cross-handler ingestion contract: incomplete evidence never blocks new starts."""

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


def make_session(tmp_path):
    return ChargePointSession(
        "cp-a", SimpleNamespace(last_frame="ingestion"),
        TransactionArchive(tmp_path), EventStore(tmp_path),
    )


def archived(tmp_path, directory):
    return [json.loads(p.read_text()) for p in (tmp_path / directory).glob("*/*.json")]


@pytest.mark.asyncio
async def test_orphan_meter_new_start_and_late_stop_survive_restart_without_inference(tmp_path):
    first = make_session(tmp_path)
    orphan = {
        "connector_id": 1, "transaction_id": 700,
        "meter_value": [{"timestamp": "2026-10-13T11:00:00Z"}],
    }
    await first.on_meter_values(**orphan)
    started = await first.on_start_transaction(**START)
    assert started.transaction_id != 700

    restarted = make_session(tmp_path)
    newer = await restarted.on_start_transaction(
        **{**START, "meter_start": 200, "timestamp": "2026-10-13T13:00:00Z"}
    )
    assert newer.transaction_id not in {700, started.transaction_id}
    late_stop = {
        "transaction_id": started.transaction_id, "meter_stop": 180,
        "timestamp": "2026-10-13T12:30:00Z",
    }
    await restarted.on_stop_transaction(**late_stop)
    by_id = {r["transaction_id"]: r for r in archived(tmp_path, "transactions")}
    assert by_id[700]["start"] is None
    assert by_id[700]["meter_values"] == [orphan]
    assert by_id[700]["stop"] is None
    assert by_id[started.transaction_id]["stop"] == late_stop
    assert by_id[newer.transaction_id]["stop"] is None
    assert all(r.get("recovery") is None for r in by_id.values())
    with sqlite3.connect(restarted.events.path) as db:
        states = dict(db.execute("SELECT transaction_id, state FROM transactions"))
    assert states[started.transaction_id] == "stopped"
    assert states[newer.transaction_id] == "open"
    assert 700 not in states


@pytest.mark.asyncio
async def test_conflicting_stop_does_not_poison_subsequent_genuine_stop(tmp_path):
    session = make_session(tmp_path)
    started = await session.on_start_transaction(**START)
    conflicting = {
        "transaction_id": started.transaction_id, "meter_stop": 90,
        "timestamp": "2026-10-13T11:00:00Z",
    }
    await session.on_stop_transaction(**conflicting)
    assert len(archived(tmp_path, "transactions-unresolved")) == 1
    with sqlite3.connect(session.events.path) as db:
        assert db.execute(
            "SELECT state FROM transactions WHERE transaction_id = ?",
            (started.transaction_id,),
        ).fetchone() == ("open",)
    genuine = {
        "transaction_id": started.transaction_id, "meter_stop": 180,
        "timestamp": "2026-10-13T12:30:00Z",
    }
    await make_session(tmp_path).on_stop_transaction(**genuine)
    records = archived(tmp_path, "transactions")
    assert len(records) == 1
    assert records[0]["stop"] == genuine
    assert len(archived(tmp_path, "transactions-unresolved")) == 1
