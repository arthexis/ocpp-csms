"""Experiment: verify inherited @on routes before moving production handlers."""

import pytest
from ocpp.messages import Call, CallResult
from ocpp.routing import on
from ocpp.v16 import ChargePoint as OcppChargePoint, call_result


class InheritedHandlers:
    @on("Heartbeat")
    async def on_heartbeat(self, **payload):
        self.handled_heartbeat = True
        return call_result.HeartbeatPayload(current_time="2026-10-10T00:00:00Z")


class ExperimentSession(InheritedHandlers, OcppChargePoint):
    pass


@pytest.mark.asyncio
async def test_inherited_decorated_handler_is_registered_and_dispatched():
    session = ExperimentSession("test-charger", None)
    assert "Heartbeat" in session.route_map
    response = await session._handle_call(Call("test-1", "Heartbeat", {}))
    assert isinstance(response, CallResult)
    assert response.unique_id == "test-1"
    assert response.payload["currentTime"] == "2026-10-10T00:00:00Z"
    assert session.handled_heartbeat is True
