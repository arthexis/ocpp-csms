import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest

from ocpp_csms.events import DATABASE_FILENAME, EventStore
from ocpp_csms.session import ChargePointSession
from ocpp_csms.transactions import TransactionArchive


START = {
    "connector_id": 1,
    "id_tag": "card-a",
    "meter_start": 100,
    "timestamp": "2026-10-02T12:00:00Z",
}


def transaction_records(tmp_path):
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (tmp_path / "transactions").glob("*/*.json")
    ]


def unresolved_records(tmp_path):
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (tmp_path / "transactions-unresolved").glob("*/*.json")
    ]


def runtime_events(tmp_path):
    with closing(sqlite3.connect(tmp_path / DATABASE_FILENAME)) as connection:
        return connection.execute(
            "SELECT event, charger_id, details_json FROM runtime_events ORDER BY id"
        ).fetchall()


def derived_transactions(tmp_path):
    with closing(sqlite3.connect(tmp_path / DATABASE_FILENAME)) as connection:
        return connection.execute(
            "SELECT transaction_id, charger_id, state, started_at, stopped_at "
            "FROM transactions ORDER BY transaction_id"
        ).fetchall()


@pytest.mark.asyncio
async def test_unanswered_start_retry_reuses_same_local_id_after_restart(tmp_path):
    archive = TransactionArchive(tmp_path)
    first = await archive.start("charger-a", START)

    restarted = TransactionArchive(tmp_path)
    retry = await restarted.start("charger-a", START)

    assert first == 1
    assert retry == first
    records = transaction_records(tmp_path)
    assert len(records) == 1
    assert records[0]["origin"] == "local"
    assert records[0]["status"] == "open"


@pytest.mark.asyncio
async def test_unknown_queued_stop_adopts_exact_historic_id_without_advancing_allocator(tmp_path):
    archive = TransactionArchive(tmp_path)

    decision = await archive.stop(
        "charger-a",
        {
            "transaction_id": 225,
            "meter_stop": 180,
            "timestamp": "2026-10-02T12:10:00Z",
        },
    )

    assert decision == {
        "event": "historical_transaction_recovered",
        "transaction_id": 225,
        "message_type": "StopTransaction",
        "decision": "adopted",
    }
    records = transaction_records(tmp_path)
    assert len(records) == 1
    assert records[0]["transaction_id"] == 225
    assert records[0]["origin"] == "recovered"
    assert records[0]["start"] is None
    assert records[0]["status"] == "stopped"

    assert await archive.start("charger-a", START) == 1


@pytest.mark.asyncio
async def test_meter_then_stop_continues_same_recovered_transaction_across_restart(tmp_path):
    archive = TransactionArchive(tmp_path)
    meter_payload = {
        "connector_id": 1,
        "transaction_id": 225,
        "meter_value": [{"timestamp": "2026-10-02T12:05:00Z"}],
    }

    first = await archive.meter_values("charger-a", meter_payload)
    assert first["event"] == "historical_transaction_recovered"
    assert first["decision"] == "adopted"

    restarted = TransactionArchive(tmp_path)
    second = await restarted.stop(
        "charger-a",
        {
            "transaction_id": 225,
            "meter_stop": 180,
            "timestamp": "2026-10-02T12:10:00Z",
        },
    )

    assert second["event"] == "historical_transaction_evidence_attached"
    assert second["decision"] == "attached"

    records = transaction_records(tmp_path)
    assert len(records) == 1
    recovered = records[0]
    assert recovered["transaction_id"] == 225
    assert recovered["origin"] == "recovered"
    assert recovered["start"] is None
    assert recovered["meter_values"] == [meter_payload]
    assert recovered["status"] == "stopped"
    assert recovered["stop"]["transaction_id"] == 225

    next_start = {**START, "timestamp": "2026-10-02T12:20:00Z"}
    assert await restarted.start("charger-a", next_start) == 1


@pytest.mark.asyncio
async def test_multiple_historic_ids_do_not_move_local_sequence_even_after_restart(tmp_path):
    archive = TransactionArchive(tmp_path)
    for transaction_id in (225, 221, 218):
        await archive.stop(
            "charger-a",
            {
                "transaction_id": transaction_id,
                "meter_stop": transaction_id,
                "timestamp": "2026-10-02T12:10:00Z",
            },
        )

    restarted = TransactionArchive(tmp_path)
    assert await restarted.start("charger-a", START) == 1


@pytest.mark.asyncio
async def test_historic_collision_preserves_evidence_without_mutating_local_record(tmp_path):
    archive = TransactionArchive(tmp_path)
    local_id = await archive.start("charger-a", START)
    assert local_id == 1

    decision = await archive.stop(
        "charger-a",
        {
            "transaction_id": local_id,
            "meter_stop": 50,
            "timestamp": "2026-10-02T11:30:00Z",
        },
    )

    assert decision["event"] == "historical_transaction_id_collision"
    assert decision["transaction_id"] == local_id
    assert decision["message_type"] == "StopTransaction"
    assert decision["decision"] == "preserved_unresolved"
    assert decision["reason"] == "message_predates_local_start"

    records = transaction_records(tmp_path)
    assert len(records) == 1
    assert records[0]["transaction_id"] == local_id
    assert records[0]["origin"] == "local"
    assert records[0]["status"] == "open"
    assert records[0]["stop"] is None

    unresolved = unresolved_records(tmp_path)
    assert len(unresolved) == 1
    assert unresolved[0]["transaction_id"] == local_id
    assert unresolved[0]["message_type"] == "StopTransaction"
    assert unresolved[0]["reason"] == "message_predates_local_start"


@pytest.mark.asyncio
async def test_session_recovered_stop_creates_stopped_history_without_synthetic_open_session(tmp_path):
    session = ChargePointSession(
        "charger-a",
        SimpleNamespace(last_frame="historic-stop"),
        TransactionArchive(tmp_path),
        EventStore(tmp_path),
    )

    await session.on_stop_transaction(
        transaction_id=225,
        meter_stop=180,
        timestamp="2026-10-02T12:10:00Z",
    )

    rows = derived_transactions(tmp_path)
    assert rows == [(225, "charger-a", "stopped", None, "2026-10-02T12:10:00Z")]
    assert not [row for row in rows if row[2] == "open"]

    events = runtime_events(tmp_path)
    assert [row[0] for row in events] == ["historical_transaction_recovered"]
    details = json.loads(events[0][2])
    assert details["transaction_id"] == 225
    assert details["message_type"] == "StopTransaction"
    assert details["decision"] == "adopted"


@pytest.mark.asyncio
async def test_session_collision_keeps_local_sqlite_state_and_emits_both_diagnostics(tmp_path):
    session = ChargePointSession(
        "charger-a",
        SimpleNamespace(last_frame="collision-stop"),
        TransactionArchive(tmp_path),
        EventStore(tmp_path),
    )

    start = await session.on_start_transaction(**START)
    assert start.transaction_id == 1

    await session.on_stop_transaction(
        transaction_id=1,
        meter_stop=50,
        timestamp="2026-10-02T11:30:00Z",
    )

    rows = derived_transactions(tmp_path)
    assert rows == [(1, "charger-a", "open", "2026-10-02T12:00:00Z", None)]

    events = runtime_events(tmp_path)
    assert [row[0] for row in events] == [
        "historical_transaction_id_collision",
        "unresolved_queued_message_preserved",
    ]
    for event, charger_id, details_json in events:
        assert charger_id == "charger-a"
        details = json.loads(details_json)
        assert details["transaction_id"] == 1
        assert details["message_type"] == "StopTransaction"
        assert details["decision"] == "preserved_unresolved"
        assert details["reason"] == "message_predates_local_start"
        assert details["unresolved_path"].startswith("transactions-unresolved/")
