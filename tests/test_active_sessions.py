import asyncio
from types import SimpleNamespace

import pytest

import ocpp_csms.server as server_module
from ocpp_csms.server import CSMSServer, OCPP_16_SUBPROTOCOL


class RuntimeEvents:
    def record_runtime(self, event, *, charger_id=None, details=None):
        pass


class WebSocket:
    def __init__(self):
        self.subprotocol = OCPP_16_SUBPROTOCOL
        self.closed = asyncio.Event()


@pytest.mark.asyncio
async def test_server_exposes_current_session_and_preserves_replacement(monkeypatch):
    class Session:
        def __init__(self, charge_point_id, connection, transactions, events):
            self.charge_point_id = charge_point_id
            self.connection = connection

        async def start(self):
            await self.connection.websocket.closed.wait()

    monkeypatch.setattr(server_module, "ChargePointSession", Session)
    server = CSMSServer("127.0.0.1", 9000, SimpleNamespace(), RuntimeEvents())
    first = WebSocket()
    second = WebSocket()

    first_task = asyncio.create_task(server.accept(first, "/charger-a"))
    await asyncio.sleep(0)
    first_session = server.session("charger-a")

    assert first_session is not None
    assert first_session.charge_point_id == "charger-a"
    assert server.session("missing") is None

    second_task = asyncio.create_task(server.accept(second, "/charger-a"))
    await asyncio.sleep(0)
    second_session = server.session("charger-a")

    assert second_session is not None
    assert second_session is not first_session

    first.closed.set()
    await first_task
    assert server.session("charger-a") is second_session

    second.closed.set()
    await second_task
    assert server.session("charger-a") is None
