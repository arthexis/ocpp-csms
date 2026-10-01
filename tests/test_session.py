from types import SimpleNamespace

import pytest

from ocpp_csms.session import ChargePointSession


@pytest.mark.asyncio
async def test_authorize_is_permissive():
    response = await ChargePointSession.on_authorize(None, id_tag="any-card")
    assert response.id_tag_info["status"] == "Accepted"


@pytest.mark.asyncio
async def test_start_transaction_is_permissive_and_allocates_ids():
    session = SimpleNamespace(_next_transaction_id=1)

    first = await ChargePointSession.on_start_transaction(session, id_tag="card-a")
    second = await ChargePointSession.on_start_transaction(session, id_tag="card-b")

    assert first.transaction_id == 1
    assert second.transaction_id == 2
    assert first.id_tag_info["status"] == "Accepted"
    assert second.id_tag_info["status"] == "Accepted"


@pytest.mark.asyncio
async def test_stop_and_meter_values_do_not_require_prior_transaction_state():
    stop = await ChargePointSession.on_stop_transaction(None, transaction_id=999)
    meter = await ChargePointSession.on_meter_values(None, transaction_id=999, meter_value=[])

    assert stop is not None
    assert meter is not None
