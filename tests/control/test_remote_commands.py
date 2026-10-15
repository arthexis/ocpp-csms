from types import SimpleNamespace

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.transactions.archive import TransactionArchive

import pytest
from ocpp.v16 import call



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


@pytest.mark.asyncio
async def test_remote_start_remains_available_with_historical_open_transaction(
    charge_point_session, tmp_path,
):
    """Historical incomplete transactions cannot veto a legitimate live remote start."""
    archive = TransactionArchive(tmp_path)
    payload = {
        "connector_id": 1,
        "id_tag": "OLD",
        "meter_start": 100,
        "timestamp": "2026-10-02T12:00:00Z",
    }
    first = EventStore(tmp_path).commit_start("charger-a", payload)
    await archive.diagnostic_start("charger-a", payload, first)
    assert first > 0
    assert archive._paths[first].exists()

    session = charge_point_session
    session.transactions = archive
    sent = capture_calls(session)
    response = await session.remote_start(id_tag="NEW", connector_id=1)

    assert response.status == "Accepted"
    assert len(sent) == 1
    assert isinstance(sent[0], call.RemoteStartTransactionPayload)
    assert sent[0].id_tag == "NEW"
    assert sent[0].connector_id == 1
    assert archive._paths[first].exists()


@pytest.mark.asyncio
async def test_restarted_server_keeps_historical_open_distinct_from_connections(tmp_path):
    from ocpp_csms.evidence.store import EventStore
    from ocpp_csms.server import CSMSServer

    archive = TransactionArchive(tmp_path)
    old_id = EventStore(tmp_path).commit_start("charger-a", {
        "connector_id": 1, "id_tag": "OLD", "meter_start": 100,
        "timestamp": "2026-10-02T12:00:00Z",
    })
    assert old_id > 0
    restarted = CSMSServer(
        host="127.0.0.1", port=0,
        transactions=TransactionArchive(tmp_path),
        events=EventStore(tmp_path),
    )
    assert restarted.connected_chargers() == []
    # This API reports SQLite history, not live connector occupancy.
    assert restarted.active_transactions("charger-a") == [(old_id, 1)]
    assert restarted.active_transaction_ids("charger-a") == [old_id]


@pytest.mark.asyncio
async def test_orphan_and_conflicting_evidence_cannot_create_live_occupancy(tmp_path):
    from ocpp_csms.evidence.store import EventStore
    from ocpp_csms.server import CSMSServer

    archive = TransactionArchive(tmp_path)
    await archive.meter_values("charger-b", {
        "transaction_id": 900000, "connector_id": 2,
        "meter_value": [{"timestamp": "2026-10-02T12:01:00Z",
                         "sampled_value": [{"value": "100"}]}],
    })
    await archive.stop("charger-b", {
        "transaction_id": 900000, "meter_stop": 200,
        "timestamp": "2026-10-02T12:02:00Z",
    })
    restarted = CSMSServer(
        host="127.0.0.1", port=0,
        transactions=TransactionArchive(tmp_path),
        events=EventStore(tmp_path),
    )
    assert restarted.active_transactions("charger-a") == []
    assert restarted.active_transactions("charger-b") == []
