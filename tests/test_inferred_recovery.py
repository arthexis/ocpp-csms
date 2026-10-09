"""Inferred transaction endings preserve original evidence and accept late OCPP stops."""
import asyncio
import json
import sqlite3

from ocpp_csms.events import EventStore
from ocpp_csms.schema import database_path, inspect_schema
from ocpp_csms.transactions import TransactionArchive
from ocpp_csms.transaction_query import TransactionQuery


def test_inferred_stop_archive_and_database_then_reconcile(tmp_path):
    async def exercise():
        archive = TransactionArchive(tmp_path)
        store = EventStore(tmp_path)
        start = {
            "connector_id": 1, "id_tag": "TAG",
            "meter_start": 10, "timestamp": "2026-10-08T20:00:00Z",
        }
        txid = await archive.start("SIM001", start)
        store.record_transaction_start(txid, "SIM001", start)
        assert await archive.infer_stop(txid)
        assert store.infer_transaction_stop(txid)
        assert not await archive.infer_stop(txid)
        assert not store.infer_transaction_stop(txid)
        view = TransactionQuery(tmp_path).get(txid)
        assert view.status == "disconnected"
        assert not view.active
        assert view.record["stop"] is None
        assert view.record["recovery"]["reason"] == "disconnected_timeout"
        with sqlite3.connect(database_path(tmp_path)) as db:
            assert db.execute("SELECT state FROM transactions WHERE transaction_id=?", (txid,)).fetchone()[0] == "inferred_stopped"
            assert db.execute("SELECT reconciled_at FROM transaction_recoveries WHERE transaction_id=?", (txid,)).fetchone()[0] is None
        stop = {"transaction_id": txid, "meter_stop": 22, "timestamp": "2026-10-08T20:15:00Z"}
        await archive.stop("SIM001", stop)
        store.record_transaction_stop("SIM001", stop)
        view = TransactionQuery(tmp_path).get(txid)
        assert view.status == "stopped"
        assert view.record["stop"]["meter_stop"] == 22
        assert view.record["recovery"]["reconciled_at"]
        with sqlite3.connect(database_path(tmp_path)) as db:
            assert db.execute("SELECT state, meter_stop FROM transactions WHERE transaction_id=?", (txid,)).fetchone() == ("stopped", 22)
            assert db.execute("SELECT reconciled_at FROM transaction_recoveries WHERE transaction_id=?", (txid,)).fetchone()[0]
    asyncio.run(exercise())


def test_schema_v6_initialization(tmp_path):
    EventStore(tmp_path)
    assert inspect_schema(tmp_path).version == 6
