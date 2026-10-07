from types import SimpleNamespace

import pytest
from ocpp.v16 import call

from ocpp_csms.events import EventStore
from ocpp_csms.session import ChargePointSession
from ocpp_csms.transactions import TransactionArchive


def charge_point_session:
    return ChargePointSession(
        "charger-a",
        SimpleNamespace(last_frame="test"),
        TransactionArchive(tmp_path),
        EventStore(tmp_path),
    )


def capture_calls(session, response=None, status="Accepted"):
    sent = []

    async def send(payload):
        sent.append(payload)
        if response is not None:
            return response
        return SimpleNamespace(status=status)

    session.call = send
    return sent


@pytest.mark.asyncio
async def test_remote_start_sends_ocpp_request(charge_point_session):
    session = charge_point_session
    sent = capture_calls(session)

    response = await session.remote_start(id_tag="REMOTE", connector_id=2)

    assert response.status == "Accepted"
    assert len(sent) == 1
    assert isinstance(sent[0], call.RemoteStartTransactionPayload)
    assert sent[0].id_tag == "REMOTE"
    assert sent[0].connector_id == 2


@pytest.mark.asyncio
async def test_remote_start_allows_unspecified_connector(charge_point_session):
    session = charge_point_session
    sent = capture_calls(session)

    await session.remote_start(id_tag="REMOTE")

    assert isinstance(sent[0], call.RemoteStartTransactionPayload)
    assert sent[0].connector_id is None


@pytest.mark.asyncio
async def test_remote_stop_sends_transaction_id(charge_point_session):
    session = charge_point_session
    sent = capture_calls(session)

    response = await session.remote_stop(42)

    assert response.status == "Accepted"
    assert len(sent) == 1
    assert isinstance(sent[0], call.RemoteStopTransactionPayload)
    assert sent[0].transaction_id == 42


@pytest.mark.asyncio
@pytest.mark.parametrize("reset_type", ["Soft", "Hard"])
async def test_reset_sends_requested_reset_type(charge_point_session, reset_type):
    session = charge_point_session
    sent = capture_calls(session)

    response = await session.reset(reset_type)

    assert response.status == "Accepted"
    assert len(sent) == 1
    assert isinstance(sent[0], call.ResetPayload)
    assert sent[0].type == reset_type


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("keys", "expected_keys"),
    [
        (None, None),
        (["HeartbeatInterval", "GetConfigurationMaxKeys"], ["HeartbeatInterval", "GetConfigurationMaxKeys"]),
    ],
)
async def test_get_configuration_preserves_key_selection(charge_point_session, keys, expected_keys):
    session = charge_point_session
    returned = SimpleNamespace(configuration_key=[], unknown_key=[])
    sent = capture_calls(session, response=returned)

    response = await session.get_configuration(keys)

    assert response is returned
    assert len(sent) == 1
    assert isinstance(sent[0], call.GetConfigurationPayload)
    assert sent[0].key == expected_keys
