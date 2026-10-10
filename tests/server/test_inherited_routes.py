"""Experiment: verify inherited @on routes before moving production handlers."""

import json

import pytest
from ocpp.messages import Call
from ocpp.routing import on
from ocpp.v16 import ChargePoint as OcppChargePoint, call_result


class InheritedHandlers:
    @on("Heartbeat")
    async def on_heartbeat(self, **payload):
        self.handled_heartbeat = True
        return call_result.HeartbeatPayload(current_time="2026-10-10T00:00:00Z")


class ExperimentSession(InheritedHandlers, OcppChargePoint):
    pass


class RecordingConnection:
    def __init__(self):
        self.sent = []

    async def send(self, message):
        self.sent.append(json.loads(message))


@pytest.mark.asyncio
async def test_inherited_decorated_handler_is_registered_and_dispatched():
    connection = RecordingConnection()
    session = ExperimentSession("test-charger", connection)
    assert "Heartbeat" in session.route_map

    await session._handle_call(Call("test-1", "Heartbeat", {}))

    assert session.handled_heartbeat is True
    assert connection.sent == [
        [3, "test-1", {"currentTime": "2026-10-10T00:00:00Z"}]
    ]
