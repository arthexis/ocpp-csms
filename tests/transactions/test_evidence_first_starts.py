import sqlite3

import pytest

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.session import ChargePointSession
from ocpp_csms.transactions.archive import TransactionArchive
from types import SimpleNamespace


START = {"connector_id": 1, "id_tag": "card", "meter_start": 100,
         "timestamp": "2026-10-13T12:00:00Z"}


@pytest.mark.asyncio
async def test_new_start_is_not_blocked_by_previous_incomplete_start(tmp_path):
    session = ChargePointSession("cp-a", SimpleNamespace(last_frame="test"),
                                 TransactionArchive(tmp_path), EventStore(tmp_path))
    first = await session.on_start_transaction(**START)
    second = await session.on_start_transaction(
        **{**START, "meter_start": 200, "timestamp": "2026-10-13T13:00:00Z"})
    assert second.transaction_id != first.transaction_id
    assert first.id_tag_info["status"] == second.id_tag_info["status"] == "Accepted"
    with sqlite3.connect(session.events.path) as db:
        rows = db.execute("SELECT transaction_id, state FROM transactions ORDER BY transaction_id").fetchall()
    assert rows == [(first.transaction_id, "open"), (second.transaction_id, "open")]


@pytest.mark.asyncio
async def test_local_allocator_skips_sqlite_only_id(tmp_path):
    events = EventStore(tmp_path)
    events.record_transaction_start(1, "cp-a", START)
    archive = TransactionArchive(tmp_path)
    assert await archive.start("cp-a", START) == 2


@pytest.mark.asyncio
async def test_allocator_preserves_sqlite_reservation_after_restart(tmp_path):
    events = EventStore(tmp_path)
    events.record_transaction_start(1, "cp-a", START)
    archive = TransactionArchive(tmp_path)
    first = await archive.start("cp-a", START)
    assert first == 2
    restarted = TransactionArchive(tmp_path)
    next_start = {**START, "meter_start": 300, "timestamp": "2026-10-13T14:00:00Z"}
    assert await restarted.start("cp-a", next_start) == 3


@pytest.mark.asyncio
async def test_allocator_uses_cached_sqlite_reservations_when_locked(tmp_path):
    events = EventStore(tmp_path)
    events.record_transaction_start(1, "cp-a", START)
    archive = TransactionArchive(tmp_path)
    # Prime the reservation cache while SQLite is readable.
    assert archive._next_available_local_id() == 2
    lock = sqlite3.connect(events.path)
    lock.execute("BEGIN EXCLUSIVE")
    try:
        assert await archive.start("cp-a", START) == 2
    finally:
        lock.rollback()
        lock.close()
