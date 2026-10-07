import pytest
from ocpp.v16 import call, call_result

from tests.rfid.helpers import make_session


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
