import json
import logging
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest
from ocpp.v16 import call, call_result

from ocpp_csms.events import DATABASE_FILENAME, EventStore
from ocpp_csms.server import RecordedWebSocket
from ocpp_csms.session import ChargePointSession
from ocpp_csms.transactions import TransactionArchive


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


@pytest.mark.asyncio
async def test_websocket_keeps_exact_latest_frame():
    raw = '[2,"abc","Heartbeat",{}]'

    class WebSocket:
        async def recv(self):
            return raw

    websocket = RecordedWebSocket(WebSocket())

    assert await websocket.recv() == raw
    assert websocket.last_frame == raw


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
async def test_authorize_is_permissive_and_records_request(tmp_path):
    session, recorded = make_session(tmp_path)

    response = await session.on_authorize(id_tag="any-card")

    assert isinstance(response, call_result.AuthorizePayload)
    assert response.id_tag_info["status"] == "Accepted"
    assert recorded == [("Authorize", {"id_tag": "any-card"}, "in", None)]


@pytest.mark.asyncio
async def test_start_transaction_is_permissive_and_persisted(tmp_path):
    session, recorded = make_session(tmp_path)

    response = await session.on_start_transaction(
        connector_id=1,
        id_tag="card-a",
        timestamp="2026-10-01T15:00:00Z",
        meter_start=10,
    )

    assert isinstance(response, call_result.StartTransactionPayload)
    assert response.transaction_id == 1
    assert response.id_tag_info["status"] == "Accepted"
    records = transaction_records(tmp_path)
    assert len(records) == 1
    assert records[0]["id_tag"] == "card-a"
    assert recorded == [
        (
            "StartTransaction",
            {
                "connector_id": 1,
                "id_tag": "card-a",
                "timestamp": "2026-10-01T15:00:00Z",
                "meter_start": 10,
            },
            "in",
            1,
        )
    ]


@pytest.mark.asyncio
async def test_authorize_uses_rfid_allow_list(tmp_path):
    (tmp_path / "rfid.csv").write_text(
        "rfid,name,enabled\nCARD-A,Alice,true\nCARD-B,Former,false\n",
        encoding="utf-8",
    )
    session, _ = make_session(tmp_path)

    accepted = await session.on_authorize(id_tag="CARD-A")
    blocked = await session.on_authorize(id_tag="CARD-B")
    missing = await session.on_authorize(id_tag="CARD-X")

    assert accepted.id_tag_info["status"] == "Accepted"
    assert blocked.id_tag_info["status"] == "Blocked"
    assert missing.id_tag_info["status"] == "Invalid"


@pytest.mark.asyncio
async def test_rejected_start_transaction_is_not_persisted(tmp_path):
    (tmp_path / "rfid.csv").write_text("CARD-A\n", encoding="utf-8")
    session, recorded = make_session(tmp_path)

    response = await session.on_start_transaction(
        connector_id=1,
        id_tag="CARD-X",
        timestamp="2026-10-07T04:00:00Z",
        meter_start=10,
    )

    assert response.transaction_id == 0
    assert response.id_tag_info["status"] == "Invalid"
    assert transaction_records(tmp_path) == []
    assert recorded == [
        (
            "StartTransaction",
            {
                "connector_id": 1,
                "id_tag": "CARD-X",
                "timestamp": "2026-10-07T04:00:00Z",
                "meter_start": 10,
            },
            "in",
            0,
        )
    ]


@pytest.mark.asyncio
async def test_invalid_rfid_file_blocks_authorize_and_start(tmp_path):
    (tmp_path / "rfid.csv").write_text(
        "rfid,enabled\nCARD-A,maybe\n",
        encoding="utf-8",
    )
    session, _ = make_session(tmp_path)

    authorize = await session.on_authorize(id_tag="CARD-A")
    start = await session.on_start_transaction(
        connector_id=1,
        id_tag="CARD-A",
        timestamp="2026-10-07T04:00:00Z",
        meter_start=10,
    )

    assert authorize.id_tag_info["status"] == "Blocked"
    assert start.id_tag_info["status"] == "Blocked"
    assert transaction_records(tmp_path) == []


@pytest.mark.asyncio
async def test_start_retry_keeps_identity_when_sqlite_is_locked(tmp_path):
    events = EventStore(tmp_path)
    archive = TransactionArchive(tmp_path)
    session = ChargePointSession(
        "charger-a",
        SimpleNamespace(last_frame="start"),
        archive,
        events,
    )
    payload = {
        "connector_id": 1,
        "id_tag": "card-a",
        "timestamp": "2026-10-02T12:00:00Z",
        "meter_start": 100,
    }

    lock = sqlite3.connect(tmp_path / DATABASE_FILENAME)
    lock.execute("BEGIN EXCLUSIVE")
    try:
        first = await session.on_start_transaction(**payload)
        retry = await session.on_start_transaction(**payload)
    finally:
        lock.rollback()
        lock.close()

    assert first.transaction_id == 1
    assert retry.transaction_id == first.transaction_id
    assert len(transaction_records(tmp_path)) == 1


@pytest.mark.asyncio
async def test_stop_and_meter_values_recover_unknown_transaction(tmp_path):
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
    records = transaction_records(tmp_path)
    assert len(records) == 1
    assert records[0]["transaction_id"] == 999
    assert records[0]["status"] == "stopped"
    assert [(entry[0], entry[2]) for entry in recorded] == [
        ("MeterValues", "in"),
        ("StopTransaction", "in"),
    ]


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
async def test_local_list_transport_uses_ocpp_16_messages(tmp_path):
    session, recorded = make_session(tmp_path)
    sent = []

    async def fake_call(payload):
        sent.append(payload)
        if isinstance(payload, call.GetLocalListVersionPayload):
            return call_result.GetLocalListVersionPayload(list_version=6)
        return call_result.SendLocalListPayload(status="Accepted")

    session.call = fake_call

    version = await session.get_local_list_version()
    result = await session.send_local_list(
        7,
        [{"rfid": "CARD-A", "name": "Alice", "enabled": True}],
    )

    assert version.list_version == 6
    assert result.status == "Accepted"
    assert isinstance(sent[0], call.GetLocalListVersionPayload)
    assert isinstance(sent[1], call.SendLocalListPayload)
    assert sent[1].list_version == 7
    assert sent[1].update_type == "Full"
    assert sent[1].local_authorization_list == [
        {"id_tag": "CARD-A", "id_tag_info": {"status": "Accepted"}}
    ]
    assert [item[0] for item in recorded] == [
        "GetLocalListVersion",
        "GetLocalListVersion",
        "SendLocalList",
        "SendLocalList",
    ]


@pytest.mark.asyncio
async def test_clear_local_list_omits_empty_authorization_list(tmp_path):
    session, _ = make_session(tmp_path)
    sent = []

    async def fake_call(payload):
        sent.append(payload)
        return call_result.SendLocalListPayload(status="Accepted")

    session.call = fake_call

    await session.send_local_list(0, [])

    assert sent[0].list_version == 0
    assert sent[0].update_type == "Full"
    assert sent[0].local_authorization_list is None
