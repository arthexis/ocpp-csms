import asyncio
from types import SimpleNamespace

import pytest

import ocpp_csms.server as server_module
from ocpp_csms.server import CSMSServer, OCPP_16_SUBPROTOCOL


class RuntimeEvents:
    def __init__(self):
        self.rows = []
        self.sessions = []

    def record_session(self, session_id, charger_id, remote_ip, remote_port):
        self.sessions.append((session_id, charger_id, remote_ip, remote_port))

    def record_runtime(self, event, *, charger_id=None, details=None):
        self.rows.append((event, charger_id, details))


class WebSocket:
    def __init__(self, path="/", subprotocol=OCPP_16_SUBPROTOCOL):
        self.path = path
        self.subprotocol = subprotocol
        self.closed = asyncio.Event()
        self.remote_address = ("192.0.2.10", 43210)

    async def close(self, *, code, reason):
        self.closed.set()


def install_waiting_session(monkeypatch, started=None, sessions=None):
    class Session:
        def __init__(self, charge_point_id, connection, transactions, events, *, session_id=None):
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
    assert server.session("missing") is None
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

    assert events.rows[0][0:2] == ("charger_connected", "charger-a")
    details = events.rows[0][2]
    assert details["path"] == "/ocpp/charger-a"
    assert details["charge_point_id"] == "charger-a"
    assert details["subprotocol"] == expected
    assert details["session_id"] == events.sessions[0][0]
    assert details["remote"] == "192.0.2.10:43210"

    websocket.closed.set()
    await task


@pytest.mark.asyncio
async def test_websocket_keeps_exact_latest_frame():
    raw = '[2,"abc","Heartbeat",{}]'

    class WebSocket:
        async def recv(self):
            return raw

    from ocpp_csms.server import RecordedWebSocket

    websocket = RecordedWebSocket(WebSocket())

    assert await websocket.recv() == raw
    assert websocket.last_frame == raw



@pytest.mark.asyncio
async def test_failed_superseded_close_cleans_new_session_registry(monkeypatch):
    """A failed close must not leave a connection registered without a handler."""
    install_waiting_session(monkeypatch)
    events = RuntimeEvents()
    server = make_server(events)

    class BrokenPrevious:
        async def close(self, **kwargs):
            raise OSError("close failed")

    server._active_sessions["charger-a"] = SimpleNamespace(
        connection=BrokenPrevious()
    )
    with pytest.raises(OSError, match="close failed"):
        await server.accept(WebSocket(path="/charger-a"))
    assert server.session("charger-a") is None


@pytest.mark.asyncio
async def test_cancel_during_superseded_close_cleans_new_session(monkeypatch):
    install_waiting_session(monkeypatch)
    events = RuntimeEvents()
    server = make_server(events)
    entered = asyncio.Event()

    class WaitingPrevious:
        async def close(self, **kwargs):
            entered.set()
            await asyncio.Event().wait()

    server._active_sessions["charger-a"] = SimpleNamespace(
        connection=WaitingPrevious()
    )
    pending = asyncio.create_task(server.accept(WebSocket(path="/charger-a")))
    await asyncio.wait_for(entered.wait(), timeout=2)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert server.session("charger-a") is None


@pytest.mark.asyncio
async def test_cancelled_superseded_close_never_erases_newer_session(monkeypatch):
    install_waiting_session(monkeypatch)
    events = RuntimeEvents()
    server = make_server(events)
    entered = asyncio.Event()

    class WaitingPrevious:
        async def close(self, **kwargs):
            entered.set()
            await asyncio.Event().wait()

    server._active_sessions["charger-a"] = SimpleNamespace(
        connection=WaitingPrevious()
    )
    second = WebSocket(path="/charger-a")
    second_task = asyncio.create_task(server.accept(second))
    await asyncio.wait_for(entered.wait(), timeout=2)
    third = WebSocket(path="/charger-a")
    third_task = asyncio.create_task(server.accept(third))
    await asyncio.sleep(0)
    newest = server.session("charger-a")
    assert newest is not None
    second_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await second_task
    assert server.session("charger-a") is newest
    third.closed.set()
    await asyncio.wait_for(third_task, timeout=2)
    assert server.session("charger-a") is None
