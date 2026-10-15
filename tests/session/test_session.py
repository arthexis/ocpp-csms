import json
import logging
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest
from ocpp.v16 import call_result

from ocpp_csms.evidence.store import DATABASE_FILENAME, EventStore
from ocpp_csms.session import ChargePointSession
from ocpp_csms.transactions.archive import TransactionArchive


def make_session(tmp_path):
    recorded = []
    session = ChargePointSession(
        "charger-a",
        SimpleNamespace(last_frame="test"),
        TransactionArchive(tmp_path),
        EventStore(tmp_path),
    )

    def record(action, payload, *, direction="in", transaction_id=None):
        recorded.append((action, payload, direction, transaction_id))

    session._record = record
    return session, recorded


def transaction_records(tmp_path):
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (tmp_path / "transactions").glob("*/*.json")
    ]


def event_directions(tmp_path, action):
    with closing(sqlite3.connect(tmp_path / DATABASE_FILENAME)) as connection:
        return connection.execute(
            "SELECT direction FROM events WHERE action = ? ORDER BY id",
            (action,),
        ).fetchall()


def test_record_failure_logs_raw_frame(caplog):
    raw = '[2,"abc","Heartbeat",{}]'

    class FailingEvents:
        def record_ocpp(self, *args, **kwargs):
            raise OSError("database unavailable")

    session = SimpleNamespace(
        id="charger-a",
        events=FailingEvents(),
        connection=SimpleNamespace(last_frame=raw),
    )

    with caplog.at_level(logging.ERROR, logger="ocpp_csms.session"):
        ChargePointSession._record(session, "Heartbeat", {})

    assert len(caplog.records) == 1
    assert caplog.records[0].message == f"frame {raw}"
    assert caplog.records[0].exc_info is not None


@pytest.mark.asyncio
async def test_start_does_not_acknowledge_when_sqlite_is_locked(tmp_path):
    events = EventStore(tmp_path)
    session = ChargePointSession(
        "charger-a", SimpleNamespace(last_frame="start"),
        TransactionArchive(tmp_path), events,
    )
    payload = {
        "connector_id": 1, "id_tag": "card-a",
        "timestamp": "2026-10-02T12:00:00Z", "meter_start": 100,
    }
    lock = sqlite3.connect(tmp_path / DATABASE_FILENAME)
    lock.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            await session.on_start_transaction(**payload)
    finally:
        lock.rollback()
        lock.close()
    assert transaction_records(tmp_path) == []
    assert event_directions(tmp_path, "StartTransaction") == []


@pytest.mark.asyncio
async def test_stop_and_meter_values_remain_orphan_evidence(tmp_path):
    session, recorded = make_session(tmp_path)

    meter = await session.on_meter_values(
        transaction_id=999,
        meter_value=[{"timestamp": "2026-10-01T15:05:00Z"}],
    )
    stop = await session.on_stop_transaction(
        transaction_id=999,
        timestamp="2026-10-01T15:10:00Z",
        meter_stop=20,
    )

    assert isinstance(meter, call_result.MeterValuesPayload)
    assert isinstance(stop, call_result.StopTransactionPayload)
    with sqlite3.connect(tmp_path / DATABASE_FILENAME) as db:
        assert db.execute(
            "SELECT transaction_id, state, started_at, meter_stop FROM transactions"
        ).fetchall() == [(999, "stopped", None, 20)]
        rows = db.execute("SELECT action, transaction_id, payload_json FROM events ORDER BY id").fetchall()
    assert [(action, tid) for action, tid, _ in rows] == [("MeterValues", 999), ("StopTransaction", 999)]
    assert json.loads(rows[0][2])["meter_value"][0]["timestamp"] == "2026-10-01T15:05:00Z"
    assert json.loads(rows[1][2])["meter_stop"] == 20



@pytest.mark.asyncio
async def test_outbound_evidence_is_recorded_after_successful_send(tmp_path):
    raw = '[2,"abc","Heartbeat",{}]'

    class Connection:
        last_frame = raw

        def __init__(self):
            self.sent = []

        async def send(self, message):
            self.sent.append(message)

    connection = Connection()
    session = ChargePointSession(
        "charger-a",
        connection,
        TransactionArchive(tmp_path),
        EventStore(tmp_path),
    )

    await session.route_message(raw)

    assert event_directions(tmp_path, "Heartbeat") == [("in",), ("out",)]
    assert len(connection.sent) == 1


@pytest.mark.asyncio
async def test_failed_send_does_not_record_outbound_evidence(tmp_path):
    raw = '[2,"abc","Heartbeat",{}]'

    class Connection:
        last_frame = raw

        async def send(self, message):
            raise OSError("connection lost")

    session = ChargePointSession(
        "charger-a",
        Connection(),
        TransactionArchive(tmp_path),
        EventStore(tmp_path),
    )

    with pytest.raises(OSError, match="connection lost"):
        await session.route_message(raw)

    assert event_directions(tmp_path, "Heartbeat") == [("in",)]


@pytest.mark.asyncio
async def test_committed_start_survives_json_diagnostic_failure(tmp_path, monkeypatch):
    events = EventStore(tmp_path)
    archive = TransactionArchive(tmp_path)
    session = ChargePointSession(
        "charger-a", SimpleNamespace(last_frame="start"), archive, events,
    )
    async def broken_diagnostic(*args, **kwargs):
        raise OSError("diagnostic storage unavailable")
    monkeypatch.setattr(archive, "diagnostic_start", broken_diagnostic)
    payload = {
        "connector_id": 1, "id_tag": "card-a",
        "timestamp": "2026-10-02T12:00:00Z", "meter_start": 100,
    }
    response = await session.on_start_transaction(**payload)
    assert response.transaction_id == 1
    with sqlite3.connect(tmp_path / DATABASE_FILENAME) as db:
        assert db.execute("SELECT transaction_id FROM transactions WHERE charger_id = ?", ("charger-a",)).fetchone()[0] == 1
    assert event_directions(tmp_path, "StartTransaction") == [("in",)]
    assert transaction_records(tmp_path) == []


@pytest.mark.asyncio
async def test_start_commit_precedes_response_and_retry_survives_restart(tmp_path, monkeypatch):
    events = EventStore(tmp_path)
    archive = TransactionArchive(tmp_path)
    session = ChargePointSession(
        "charger-a", SimpleNamespace(last_frame="start"), archive, events,
    )
    payload = {
        "connector_id": 1, "id_tag": "card-a",
        "timestamp": "2026-10-02T12:00:00Z", "meter_start": 100,
    }
    observed = []
    original = archive.diagnostic_start
    async def inspect_committed(charger, start, transaction_id):
        with sqlite3.connect(tmp_path / DATABASE_FILENAME) as db:
            assert db.execute("SELECT transaction_id FROM transactions WHERE charger_id = ?", (charger,)).fetchone()[0] == transaction_id
        assert event_directions(tmp_path, "StartTransaction") == [("in",)]
        observed.append(transaction_id)
        await original(charger, start, transaction_id)
    monkeypatch.setattr(archive, "diagnostic_start", inspect_committed)
    first = await session.on_start_transaction(**payload)
    restarted = ChargePointSession(
        "charger-a", SimpleNamespace(last_frame="retry"),
        TransactionArchive(tmp_path), EventStore(tmp_path),
    )
    retry = await restarted.on_start_transaction(**payload)
    assert observed == [first.transaction_id]
    assert retry.transaction_id == first.transaction_id == 1
    assert event_directions(tmp_path, "StartTransaction") == [("in",), ("in",)]
    assert len(transaction_records(tmp_path)) == 1
