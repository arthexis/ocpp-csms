from types import SimpleNamespace

import pytest



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
async def test_remote_acceptance_does_not_replace_transaction_messages(charge_point_session):
    session = charge_point_session

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
async def test_reset_request_and_confirmation_are_preserved_as_evidence(charge_point_session, reset_type):
    session = charge_point_session

    async def accepted(_payload):
        return SimpleNamespace(status="Accepted")

    session.call = accepted

    response = await session.reset(reset_type)

    assert response.status == "Accepted"
    rows = evidence(session, "Reset")
    assert [row[0] for row in rows] == ["out", "in"]
    assert reset_type in rows[0][1]
    assert "Accepted" in rows[1][1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "arguments"),
    [
        ("remote_start", {"id_tag": "CARD", "connector_id": 1}),
        ("remote_stop", {"transaction_id": 17}),
        ("reset", {"reset_type": "Soft"}),
        ("change_availability", {"connector_id": 1, "availability_type": "Inoperative"}),
        ("unlock_connector", {"connector_id": 1}),
    ],
)
async def test_outbound_record_failure_currently_does_not_block_physical_command(
    charge_point_session, monkeypatch, operation, arguments, caplog
):
    """Document current B4 behavior; deliberately do not change the policy."""
    sent = []

    def unavailable(*args, **kwargs):
        raise OSError("evidence store unavailable")

    async def send(request):
        sent.append(request)
        return SimpleNamespace(status="Accepted")

    session = charge_point_session
    monkeypatch.setattr(session.events, "record_ocpp", unavailable)
    monkeypatch.setattr(session, "call", send)
    with caplog.at_level("ERROR", logger="ocpp_csms.session"):
        response = await getattr(session, operation)(**arguments)
    assert response.status == "Accepted"
    assert len(sent) == 1
    assert "evidence store unavailable" in caplog.text
