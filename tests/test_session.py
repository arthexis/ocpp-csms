import pytest

from ocpp_csms.session import ChargePointSession


@pytest.mark.asyncio
async def test_authorize_is_permissive():
    response = await ChargePointSession.on_authorize(None, id_tag="any-card")
    assert response["idTagInfo"]["status"] == "Accepted"


@pytest.mark.asyncio
async def test_start_transaction_is_permissive():
    response = await ChargePointSession.on_start_transaction(None, id_tag="card-a")
    assert response["transactionId"] >= 1
    assert response["idTagInfo"]["status"] == "Accepted"


@pytest.mark.asyncio
async def test_stop_and_meter_values_do_not_require_prior_transaction_state():
    stop = await ChargePointSession.on_stop_transaction(None, transaction_id=999)
    meter = await ChargePointSession.on_meter_values(None, transaction_id=999, meter_value=[])

    assert stop == {}
    assert meter == {}
