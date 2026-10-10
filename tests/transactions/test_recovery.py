import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest

from ocpp_csms.evidence.store import DATABASE_FILENAME, EventStore
from ocpp_csms.session import ChargePointSession
from ocpp_csms.transactions import TransactionArchive


CHARGER = "charger-a"
START = {
    "connector_id": 1,
    "id_tag": "card-a",
    "meter_start": 100,
    "timestamp": "2026-10-02T12:00:00Z",
}


def records_in(tmp_path, directory):
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (tmp_path / directory).glob("*/*.json")
    ]


def transaction_records(tmp_path):
    return records_in(tmp_path, "transactions")


def unresolved_records(tmp_path):
    return records_in(tmp_path, "transactions-unresolved")


def stop_payload(transaction_id, *, timestamp="2026-10-02T12:10:00Z", **extra):
    return {
        "transaction_id": transaction_id,
        "meter_stop": 180,
        "timestamp": timestamp,
        **extra,
    }


def recovered_transaction(tmp_path, transaction_id):
    matches = [
        record for record in transaction_records(tmp_path)
        if record["transaction_id"] == transaction_id
    ]
    assert len(matches) == 1
    return matches[0]


def make_session(tmp_path):
    return ChargePointSession(
        CHARGER,
        SimpleNamespace(last_frame="recovery-regression"),
        TransactionArchive(tmp_path),
        EventStore(tmp_path),
    )


def derived_transactions(tmp_path):
    with closing(sqlite3.connect(tmp_path / DATABASE_FILENAME)) as connection:
        return connection.execute(
            "SELECT transaction_id, charger_id, state, started_at, stopped_at "
            "FROM transactions ORDER BY transaction_id"
        ).fetchall()


@pytest.mark.asyncio
async def test_unanswered_start_retry_reuses_same_local_id_after_restart(tmp_path):
    archive = TransactionArchive(tmp_path)
    first = await archive.start(CHARGER, START)

    retry = await TransactionArchive(tmp_path).start(CHARGER, START)

    assert first == retry == 1
    record = recovered_transaction(tmp_path, 1)
    assert record["origin"] == "local"
    assert record["status"] == "open"


@pytest.mark.asyncio
async def test_unknown_queued_stop_adopts_exact_historic_id_without_advancing_allocator(tmp_path):
    archive = TransactionArchive(tmp_path)

    decision = await archive.stop(CHARGER, stop_payload(225))

    assert decision["event"] == "historical_transaction_recovered"
    assert decision["transaction_id"] == 225
    record = recovered_transaction(tmp_path, 225)
    assert record["origin"] == "recovered"
    assert record["start"] is None
    assert record["status"] == "stopped"
    assert await archive.start(CHARGER, START) == 1


@pytest.mark.asyncio
async def test_meter_then_stop_continues_same_recovered_transaction_across_restart(tmp_path):
    archive = TransactionArchive(tmp_path)
    meter_payload = {
        "connector_id": 1,
        "transaction_id": 225,
        "meter_value": [{"timestamp": "2026-10-02T12:05:00Z"}],
    }

    first = await archive.meter_values(CHARGER, meter_payload)
    restarted = TransactionArchive(tmp_path)
    second = await restarted.stop(CHARGER, stop_payload(225))

    assert first["event"] == "historical_transaction_recovered"
    assert second["event"] == "historical_transaction_evidence_attached"
    record = recovered_transaction(tmp_path, 225)
    assert record["origin"] == "recovered"
    assert record["start"] is None
    assert record["meter_values"] == [meter_payload]
    assert record["status"] == "stopped"
    assert record["stop"]["transaction_id"] == 225
    assert await restarted.start(
        CHARGER,
        {**START, "timestamp": "2026-10-02T12:20:00Z"},
    ) == 1


@pytest.mark.asyncio
async def test_multiple_historic_ids_do_not_move_local_sequence_even_after_restart(tmp_path):
    archive = TransactionArchive(tmp_path)
    for transaction_id in (225, 221, 218):
        await archive.stop(CHARGER, stop_payload(transaction_id))

    assert await TransactionArchive(tmp_path).start(CHARGER, START) == 1


@pytest.mark.asyncio
async def test_historic_collision_preserves_evidence_without_mutating_local_record(tmp_path):
    archive = TransactionArchive(tmp_path)
    local_id = await archive.start(CHARGER, START)

    decision = await archive.stop(
        CHARGER,
        stop_payload(local_id, timestamp="2026-10-02T11:30:00Z", meter_stop=50),
    )

    assert decision["event"] == "historical_transaction_id_collision"
    assert decision["reason"] == "message_predates_local_start"

    local = recovered_transaction(tmp_path, local_id)
    assert local["origin"] == "local"
    assert local["status"] == "open"
    assert local["stop"] is None

    unresolved = unresolved_records(tmp_path)
    assert len(unresolved) == 1
    assert unresolved[0]["transaction_id"] == local_id
    assert unresolved[0]["message_type"] == "StopTransaction"
    assert unresolved[0]["reason"] == "message_predates_local_start"


@pytest.mark.asyncio
async def test_session_recovered_stop_creates_stopped_history_without_synthetic_open_session(tmp_path):
    session = make_session(tmp_path)

    await session.on_stop_transaction(**stop_payload(225))

    rows = derived_transactions(tmp_path)
    assert rows == [(225, CHARGER, "stopped", None, "2026-10-02T12:10:00Z")]
    assert not [row for row in rows if row[2] == "open"]

    record = recovered_transaction(tmp_path, 225)
    assert record["origin"] == "recovered"
    assert record["status"] == "stopped"
    assert record["start"] is None


@pytest.mark.asyncio
async def test_session_collision_keeps_local_derived_state_open(tmp_path):
    session = make_session(tmp_path)
    start = await session.on_start_transaction(**START)

    await session.on_stop_transaction(
        **stop_payload(start.transaction_id, timestamp="2026-10-02T11:30:00Z", meter_stop=50)
    )

    assert derived_transactions(tmp_path) == [
        (1, CHARGER, "open", "2026-10-02T12:00:00Z", None)
    ]
    local = recovered_transaction(tmp_path, 1)
    assert local["status"] == "open"
    assert local["stop"] is None
    assert len(unresolved_records(tmp_path)) == 1
