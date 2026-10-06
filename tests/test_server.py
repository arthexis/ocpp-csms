import asyncio
from types import SimpleNamespace

import pytest

import ocpp_csms.server as server_module
from ocpp_csms.server import CSMSServer, OCPP_16_SUBPROTOCOL


class RuntimeEvents:
    def __init__(self):
        self.rows = []

    def record_runtime(self, event, *, charger_id=None, details=None):
        self.rows.append((event, charger_id, details))


class WebSocket:
    def __init__(self, path="/", subprotocol=OCPP_16_SUBPROTOCOL):
        self.path = path
        self.subprotocol = subprotocol
        self.closed = asyncio.Event()


def install_waiting_session(monkeypatch, started=None, sessions=None):
    class Session:
        def __init__(self, charge_point_id, connection, transactions, events):
            self.charge_point_id = charge_point_id
            self.connection = connection
            if sessions is not None:
                sessions.append(self)

        async def start(self):
            if started is not None:
                started.append(self.charge_point_id)
            await self.connection.websocket.closed.wait()

    monkeypatch.setattr(server_module, "ChargePointSession", Session)


def make_server(events):
    return CSMSServer(
        "127.0.0.1",
        9000,
        SimpleNamespace(),
        events,
    )


@pytest.mark.asyncio
async def test_older_disconnect_does_not_override_newer_connection(monkeypatch):
    started = []
    sessions = []
    install_waiting_session(monkeypatch, started, sessions)
    events = RuntimeEvents()
    server = make_server(events)
    first = WebSocket(path="/charger-a")
    second = WebSocket(path="/charger-a")

    first_task = asyncio.create_task(server.accept(first))
    await asyncio.sleep(0)
    assert server.session("charger-a") is sessions[0]

    second_task = asyncio.create_task(server.accept(second))
    await asyncio.sleep(0)

    assert started == ["charger-a", "charger-a"]
    assert server.session("charger-a") is sessions[1]
    assert [row[:2] for row in events.rows] == [
        ("charger_connected", "charger-a"),
        ("charger_connected", "charger-a"),
    ]

    first.closed.set()
    await first_task

    assert server.session("charger-a") is sessions[1]
    assert [row[:2] for row in events.rows] == [
        ("charger_connected", "charger-a"),
        ("charger_connected", "charger-a"),
    ]

    second.closed.set()
    await second_task

    assert server.session("charger-a") is None
    assert events.rows[-1][:2] == ("charger_disconnected", "charger-a")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("subprotocol", "expected"),
    [
        (OCPP_16_SUBPROTOCOL, OCPP_16_SUBPROTOCOL),
        (None, None),
    ],
)
async def test_connection_records_path_identity_and_subprotocol(monkeypatch, subprotocol, expected):
    install_waiting_session(monkeypatch)
    events = RuntimeEvents()
    server = make_server(events)
    websocket = WebSocket(path="/ocpp/charger-a?source=test", subprotocol=subprotocol)

    task = asyncio.create_task(server.accept(websocket))
    await asyncio.sleep(0)

    assert events.rows[0] == (
        "charger_connected",
        "charger-a",
        {
            "path": "/ocpp/charger-a?source=test",
            "charge_point_id": "charger-a",
            "subprotocol": expected,
        },
    )

    websocket.closed.set()
    await task
