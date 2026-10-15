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
