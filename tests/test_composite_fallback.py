from types import SimpleNamespace

import pytest

from ocpp_csms.control import dispatch_control


CHARGER = "charger-a"
REQUEST = {
    "command": "get_composite_schedule",
    "connector": 0,
    "duration": 3600,
    "charging_rate_unit": "W",
}


class Session:
    def __init__(self, responses):
        self.responses = dict(responses)
        self.calls = []

    async def get_composite_schedule(self, connector_id, duration, charging_rate_unit=None):
        self.calls.append((connector_id, duration, charging_rate_unit))
        return self.responses[connector_id]


class Registry:
    def __init__(self, session, connectors=(1, 2)):
        self._session = session
        self._connectors = list(connectors)

    def session(self, charge_point_id):
        return self._session if charge_point_id == CHARGER else None

    def connected_chargers(self):
        return [CHARGER]

    def physical_connector_ids(self, charge_point_id):
        return list(self._connectors) if charge_point_id == CHARGER else []

    def active_transaction_ids(self, charge_point_id):
        return []

    def record_control_event(self, event, *, charger_id, details=None):
        pass


def composite(status, connector=None, limit=60000):
    if status != "Accepted":
        return SimpleNamespace(status=status)
    return SimpleNamespace(
        status="Accepted",
        connector_id=connector,
        schedule_start="2026-10-04T00:00:00Z",
        charging_schedule={
            "duration": 3600,
            "chargingRateUnit": "W",
            "chargingSchedulePeriod": [{"startPeriod": 0, "limit": limit}],
        },
    )


async def dispatch(responses, *, connectors=(1, 2), connector=0):
    session = Session(responses)
    request = {**REQUEST, "connector": connector}
    response = await dispatch_control(Registry(session, connectors), request)
    return response["response"], session.calls


@pytest.mark.asyncio
async def test_connector_zero_accepted_does_not_fall_back():
    payload, calls = await dispatch({0: composite("Accepted", 0)})

    assert payload["status"] == "Accepted"
    assert "compatibility_fallback" not in payload
    assert calls == [(0, 3600, "W")]


@pytest.mark.asyncio
async def test_connector_zero_rejection_falls_back_to_physical_connectors():
    payload, calls = await dispatch({
        0: composite("Rejected"),
        1: composite("Accepted", 1),
        2: composite("Accepted", 2),
    })

    assert payload["status"] == "Accepted"
    assert payload["requested_connector"] == 0
    assert payload["compatibility_fallback"] == "physical_connectors"
    assert [item["connector_id"] for item in payload["schedules"]] == [1, 2]
    assert [item["response"]["status"] for item in payload["schedules"]] == ["Accepted", "Accepted"]
    assert calls == [(0, 3600, "W"), (1, 3600, "W"), (2, 3600, "W")]


@pytest.mark.asyncio
async def test_direct_physical_connector_never_fans_out():
    payload, calls = await dispatch({1: composite("Rejected")}, connector=1)

    assert payload == {"status": "Rejected"}
    assert calls == [(1, 3600, "W")]


@pytest.mark.asyncio
async def test_partial_fallback_remains_rejected_and_preserves_each_response():
    payload, _ = await dispatch({
        0: composite("Rejected"),
        1: composite("Accepted", 1),
        2: composite("Rejected"),
    })

    assert payload["status"] == "Rejected"
    assert payload["compatibility_fallback"] == "physical_connectors"
    assert [item["response"]["status"] for item in payload["schedules"]] == ["Accepted", "Rejected"]


@pytest.mark.asyncio
async def test_connector_zero_rejection_without_known_connectors_is_preserved():
    payload, calls = await dispatch({0: composite("Rejected")}, connectors=())

    assert payload == {"status": "Rejected"}
    assert calls == [(0, 3600, "W")]
