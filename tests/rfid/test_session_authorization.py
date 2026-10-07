import pytest
from ocpp.v16 import call_result

from tests.rfid.helpers import make_session, transaction_records


@pytest.mark.asyncio
async def test_authorize_is_permissive_without_rfid_file(tmp_path):
    session, recorded = make_session(tmp_path)

    response = await session.on_authorize(id_tag="any-card")

    assert isinstance(response, call_result.AuthorizePayload)
    assert response.id_tag_info["status"] == "Accepted"
    assert recorded == [("Authorize", {"id_tag": "any-card"}, "in", None)]


@pytest.mark.asyncio
async def test_start_transaction_is_permissive_without_rfid_file(tmp_path):
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
