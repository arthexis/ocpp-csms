import asyncio
from types import SimpleNamespace

import pytest

import ocpp_csms.server as server_module
from ocpp_csms.server import CSMSServer, OCPP_16_SUBPROTOCOL


class RuntimeEvents:
    def __init__(self):
        self.rows = []

    def record_runtime(self, event, *, charger_id=None, details=None):
        self.rows.append((event, charger_id))


class WebSocket:
    subprotocol = OCPP_16_SUBPROTOCOL

    def __init__(self):
        self.closed = asyncio.Event()


@pytest.mark.asyncio
async def test_older_disconnect_does_not_override_newer_connection(monkeypatch):
    started = []

    class Session:
        def __init__(self, charge_point_id, connection, transactions, events):
            self.charge_point_id = charge_point_id
            self.connection = connection

        async def start(self):
            started.append(self.charge_point_id)
            await self.connection.websocket.closed.wait()

    monkeypatch.setattr(server_module, "ChargePointSession", Session)
    events = RuntimeEvents()
    server = CSMSServer(
        "127.0.0.1",
        9000,
        SimpleNamespace(),
        events,
    )
    first = WebSocket()
    second = WebSocket()

    first_task = asyncio.create_task(server.accept(first, "/charger-a"))
    await asyncio.sleep(0)
    second_task = asyncio.create_task(server.accept(second, "/charger-a"))
    await asyncio.sleep(0)

    assert started == ["charger-a", "charger-a"]
    assert events.rows == [
        ("charger_connected", "charger-a"),
        ("charger_connected", "charger-a"),
    ]

    first.closed.set()
    await first_task

    assert events.rows == [
        ("charger_connected", "charger-a"),
        ("charger_connected", "charger-a"),
    ]
    assert "charger-a" in server._active_connections

    second.closed.set()
    await second_task

    assert events.rows[-1] == ("charger_disconnected", "charger-a")
    assert "charger-a" not in server._active_connections
