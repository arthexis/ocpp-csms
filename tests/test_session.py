from types import SimpleNamespace

import pytest

from ocpp_csms.session import ChargePointSession
from ocpp_csms.transactions import TransactionArchive


def make_session(tmp_path):
    archive = TransactionArchive(tmp_path)
    recorded = []
    session = SimpleNamespace(id="charger-a", transactions=archive)

    def record(action, payload, *, direction="in", transaction_id=None):
        recorded.append((action, payload, direction, transaction_id))

    def reply(action, response, *, transaction_id=None):
        record(action, response, direction="out", transaction_id=transaction_id)
        return response

    session._record = record
    session._reply = reply
    return session, recorded


def test_record_failure_dumps_recent_frames():
    dumped = []

    class FailingEvents:
        def record_ocpp(self, *args, **kwargs):
            raise OSError("database unavailable")

    class Frames:
        def dump(self, logger):
            dumped.append(logger.name)

    session = SimpleNamespace(
        id="charger-a",
        events=FailingEvents(),
        frames=Frames(),
    )

    ChargePointSession._record(session, "Heartbeat", {})

    assert dumped == ["ocpp_csms.session"]


@pytest.mark.asyncio
async def test_authorize_is_permissive_and_records_request_and_reply(tmp_path):
    session, recorded = make_session(tmp_path)

    response = await ChargePointSession.on_authorize(session, id_tag="any-card")

    assert response["idTagInfo"]["status"] == "Accepted"
    assert recorded[0][0] == "Authorize"
    assert recorded[0][1]["id_tag"] == "any-card"
    assert recorded[0][2] == "in"
    assert recorded[1][0] == "Authorize"
    assert recorded[1][1]["idTagInfo"]["status"] == "Accepted"
    assert recorded[1][2] == "out"


@pytest.mark.asyncio
async def test_start_transaction_is_permissive_persisted_and_records_reply(tmp_path):
    session, recorded = make_session(tmp_path)

    response = await ChargePointSession.on_start_transaction(
        session,
        id_tag="card-a",
        timestamp="2026-10-01T15:00:00Z",
        meter_start=10,
    )

    assert response["transactionId"] == 1
    assert response["idTagInfo"]["status"] == "Accepted"
    files = list((tmp_path / "transactions" / "2026-10-01").glob("*.json"))
    assert len(files) == 1
    assert '"id_tag": "card-a"' in files[0].read_text(encoding="utf-8")
    assert recorded[0][0] == "StartTransaction"
    assert recorded[0][2] == "in"
    assert recorded[0][3] == 1
    assert recorded[1][0] == "StartTransaction"
    assert recorded[1][2] == "out"
    assert recorded[1][3] == 1


@pytest.mark.asyncio
async def test_stop_and_meter_values_recover_unknown_transaction(tmp_path):
    session, recorded = make_session(tmp_path)

    meter = await ChargePointSession.on_meter_values(
        session,
        transaction_id=999,
        meter_value=[{"timestamp": "2026-10-01T15:05:00Z"}],
    )
    stop = await ChargePointSession.on_stop_transaction(
        session,
        transaction_id=999,
        timestamp="2026-10-01T15:10:00Z",
        meter_stop=20,
    )

    assert meter == {}
    assert stop == {}
    files = list((tmp_path / "transactions").glob("*/*.json"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert '"transaction_id": 999' in text
    assert '"status": "stopped"' in text
    assert [(entry[0], entry[2]) for entry in recorded] == [
        ("MeterValues", "in"),
        ("MeterValues", "out"),
        ("StopTransaction", "in"),
        ("StopTransaction", "out"),
    ]
