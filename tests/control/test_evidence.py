from types import SimpleNamespace

import pytest

from ocpp_csms.events import EventStore
from ocpp_csms.session import ChargePointSession
from ocpp_csms.transactions import TransactionArchive


def make_session(tmp_path):
    return ChargePointSession(
        "charger-a",
        SimpleNamespace(last_frame="test"),
        TransactionArchive(tmp_path),
        EventStore(tmp_path),
    )


def transaction_state(session, transaction_id):
    with session.events._connect() as connection:
        row = connection.execute(
            "SELECT state FROM transactions WHERE transaction_id = ?",
            (transaction_id,),
        ).fetchone()
    return row[0] if row else None


def evidence(session, action):
    with session.events._connect() as connection:
        return connection.execute(
            "SELECT direction, payload_json, transaction_id FROM events WHERE action = ? ORDER BY id",
            (action,),
        ).fetchall()


@pytest.mark.asyncio
async def test_remote_acceptance_does_not_replace_transaction_messages(tmp_path):
    session = make_session(tmp_path)

    async def accepted(_payload):
        return SimpleNamespace(status="Accepted")

    session.call = accepted

    remote_start = await session.remote_start(id_tag="REMOTE", connector_id=1)
    assert remote_start.status == "Accepted"
    with session.events._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0

    start = await session.on_start_transaction(
        connector_id=1,
        id_tag="REMOTE",
        timestamp="2026-10-02T22:00:00Z",
        meter_start=100,
    )
    transaction_id = start.transaction_id
    assert transaction_state(session, transaction_id) == "open"

    remote_stop = await session.remote_stop(transaction_id)
    assert remote_stop.status == "Accepted"
    assert transaction_state(session, transaction_id) == "open"

    await session.on_stop_transaction(
        transaction_id=transaction_id,
        timestamp="2026-10-02T22:10:00Z",
        meter_stop=120,
    )
    assert transaction_state(session, transaction_id) == "stopped"

    assert [row[0] for row in evidence(session, "RemoteStartTransaction")] == ["out", "in"]
    assert [row[0] for row in evidence(session, "RemoteStopTransaction")] == ["out", "in"]
    assert [row[2] for row in evidence(session, "RemoteStopTransaction")] == [transaction_id, transaction_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("reset_type", ["Soft", "Hard"])
async def test_reset_request_and_confirmation_are_preserved_as_evidence(tmp_path, reset_type):
    session = make_session(tmp_path)

    async def accepted(_payload):
        return SimpleNamespace(status="Accepted")

    session.call = accepted

    response = await session.reset(reset_type)

    assert response.status == "Accepted"
    rows = evidence(session, "Reset")
    assert [row[0] for row in rows] == ["out", "in"]
    assert reset_type in rows[0][1]
    assert "Accepted" in rows[1][1]
