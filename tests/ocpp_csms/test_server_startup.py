"""The actual CSMS server must stay running after listeners start.

This guards against accidentally restoring the removed transaction-recovery worker.
"""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.server import CSMSServer
from ocpp_csms.transactions.archive import TransactionArchive


@pytest.mark.asyncio
async def test_server_stays_alive_after_listener_startup(tmp_path, monkeypatch):
    import ocpp_csms.server as module

    started = asyncio.Event()

    @asynccontextmanager
    async def fake_control(*args, **kwargs):
        yield

    @asynccontextmanager
    async def fake_websocket(*args, **kwargs):
        yield

    class FakeTLS:
        async def start(self, *args, **kwargs):
            started.set()
            return False

        async def stop(self):
            pass

    monkeypatch.setattr(module, "ControlServer", fake_control)
    monkeypatch.setattr(module.websockets, "serve", fake_websocket)
    monkeypatch.setattr(module, "TLSListener", FakeTLS)
    monkeypatch.setattr(module, "load_auth", lambda: {"mode": "none"})
    monkeypatch.setattr(module, "write_pid", lambda *args: None)
    monkeypatch.setattr(module, "remove_pid", lambda *args: None)

    server = CSMSServer("127.0.0.1", 0, TransactionArchive(tmp_path), EventStore(tmp_path))
    task = asyncio.create_task(server.serve_forever())
    try:
        await asyncio.wait_for(started.wait(), timeout=3)
        await asyncio.sleep(0)
        assert not task.done(), "server exited unexpectedly after startup"
        assert server._tls_listener is not None
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert server._tls_listener is None


@pytest.mark.asyncio
async def test_invalid_auth_cleans_pid_without_opening_listener(tmp_path, monkeypatch):
    """An invalid auth configuration must not leave an apparently live server."""
    import sqlite3
    from contextlib import closing

    import ocpp_csms.server as module
    from ocpp_csms.runtime import PID_FILENAME

    def reject_auth():
        raise ValueError("auth rejected")

    async def unexpected_listener(self, socket_path, auth):
        raise AssertionError("failed auth must never open listeners")

    monkeypatch.setattr(module, "load_auth", reject_auth)
    monkeypatch.setattr(module.CSMSServer, "_serve_listeners", unexpected_listener)
    server = CSMSServer(
        "127.0.0.1", 0, TransactionArchive(tmp_path), EventStore(tmp_path)
    )

    with pytest.raises(ValueError, match="auth rejected"):
        await server.serve_forever()

    assert not (tmp_path / PID_FILENAME).exists()
    with closing(sqlite3.connect(tmp_path / "ocpp-csms.sqlite3")) as db:
        events = db.execute(
            "SELECT event FROM runtime_events WHERE event LIKE 'server_%' ORDER BY id"
        ).fetchall()
    assert events == [("server_started",), ("server_stopped",)]
