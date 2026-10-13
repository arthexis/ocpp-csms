import asyncio
import sqlite3

import pytest

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.transactions.archive import TransactionArchive


START = {"connector_id": 1, "id_tag": "card", "meter_start": 100,
         "timestamp": "2026-10-13T12:00:00Z"}


@pytest.mark.asyncio
async def test_bootstrap_reserves_sqlite_only_ids_and_survives_exclusive_lock(tmp_path):
    events = EventStore(tmp_path)
    events.record_transaction_start(1, "cp-a", START)
    archive = TransactionArchive(tmp_path)
    first = await archive.start("cp-a", START)
    assert first == 2
    lock = sqlite3.connect(events.path)
    lock.execute("BEGIN EXCLUSIVE")
    try:
        second = await archive.start(
            "cp-a", {**START, "meter_start": 200,
                     "timestamp": "2026-10-13T13:00:00Z"})
        assert second == 3
    finally:
        lock.rollback()
        lock.close()
    assert await TransactionArchive(tmp_path).start(
        "cp-a", {**START, "meter_start": 300,
                 "timestamp": "2026-10-13T14:00:00Z"}) == 4


@pytest.mark.asyncio
async def test_independent_allocators_never_reuse_same_id(tmp_path):
    first = TransactionArchive(tmp_path)
    second = TransactionArchive(tmp_path)
    a, b = await asyncio.gather(
        first.start("cp-a", START),
        second.start("cp-b", START),
    )
    assert a != b


@pytest.mark.asyncio
async def test_locked_sqlite_before_first_reconciliation_fails_without_guessing(tmp_path):
    events = EventStore(tmp_path)
    events.record_transaction_start(1, "cp-a", START)
    lock = sqlite3.connect(events.path)
    lock.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            await TransactionArchive(tmp_path).start("cp-a", START)
    finally:
        lock.rollback()
        lock.close()
