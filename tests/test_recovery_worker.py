"""Recovery is based on persisted receive evidence, not connection age alone."""
import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.recovery import recover_once
from ocpp_csms.schema import database_path
from ocpp_csms.transaction_query import TransactionQuery
from ocpp_csms.transactions import TransactionArchive

NOW = datetime(2026, 10, 8, 23, tzinfo=timezone.utc)


async def setup(tmp_path, *, disconnect=True, activity_minutes=90):
    events = EventStore(tmp_path)
    archive = TransactionArchive(tmp_path)
    start = {"connector_id": 1, "id_tag": "TAG", "meter_start": 0,
             "timestamp": "2026-10-08T20:00:00Z"}
    txid = await archive.start("SIM001", start)
    events.record_transaction_start(txid, "SIM001", start)
    earlier = (NOW - timedelta(minutes=activity_minutes)).isoformat()
    with sqlite3.connect(database_path(tmp_path)) as db:
        db.execute("UPDATE transactions SET last_activity_at=? WHERE transaction_id=?",
                   (earlier, txid))
        db.execute("""INSERT INTO runtime_events(occurred_at,event,charger_id)
                      VALUES (?,?,?)""",
                   (earlier, "charger_disconnected" if disconnect else "charger_connected", "SIM001"))
    view = TransactionQuery(tmp_path).get(txid)
    record = view.record
    record["created_at"] = earlier
    record["updated_at"] = earlier
    record["start_received_at"] = earlier
    view.path.write_text(__import__("json").dumps(record))
    server = SimpleNamespace(events=events, transactions=archive,
                             connected_chargers=lambda: [])
    return server, txid


def test_aged_disconnected_transaction_recovered_once(tmp_path):
    async def run():
        server, txid = await setup(tmp_path)
        assert await recover_once(server, now=NOW) == [txid]
        assert await recover_once(server, now=NOW) == []
        view = TransactionQuery(tmp_path).get(txid)
        assert view.status == "disconnected"
        assert view.record["stop"] is None
        with sqlite3.connect(database_path(tmp_path)) as db:
            assert db.execute("SELECT state FROM transactions").fetchone()[0] == "inferred_stopped"
        # A server restart uses persisted evidence and does not repeat the update.
        restarted = SimpleNamespace(events=EventStore(tmp_path),
                                    transactions=TransactionArchive(tmp_path),
                                    connected_chargers=lambda: [])
        assert await recover_once(restarted, now=NOW) == []
    asyncio.run(run())


def test_no_recovery_if_connected_or_recent(tmp_path):
    async def run():
        server, txid = await setup(tmp_path, disconnect=False)
        assert await recover_once(server, now=NOW) == []
        server, txid = await setup(tmp_path / "recent", activity_minutes=10)
        assert await recover_once(server, now=NOW) == []
        server.connected_chargers = lambda: ["SIM001"]
        assert await recover_once(server, now=NOW + timedelta(hours=2)) == []
    asyncio.run(run())


def test_restart_after_partial_sqlite_write_retries(tmp_path):
    async def run():
        server, txid = await setup(tmp_path)
        original = server.events.infer_transaction_stop
        def fail(*args, **kwargs):
            raise sqlite3.OperationalError("temporary failure")
        server.events.infer_transaction_stop = fail
        assert await recover_once(server, now=NOW) == []
        assert TransactionQuery(tmp_path).get(txid).status == "disconnected"
        server.events.infer_transaction_stop = original
        assert await recover_once(server, now=NOW) == [txid]
        with sqlite3.connect(database_path(tmp_path)) as db:
            assert db.execute("SELECT state FROM transactions").fetchone()[0] == "inferred_stopped"
    asyncio.run(run())


def test_no_recovery_if_recent_transaction_activity(tmp_path):
    async def run():
        server, txid = await setup(tmp_path)
        with sqlite3.connect(database_path(tmp_path)) as db:
            db.execute("UPDATE transactions SET last_activity_at=? WHERE transaction_id=?",
                       (NOW.isoformat(), txid))
        assert await recover_once(server, now=NOW) == []
    asyncio.run(run())
