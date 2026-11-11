"""Regression tests for long-running GetDiagnostics calls and safe status."""
import asyncio
from types import SimpleNamespace

import pytest

from ocpp_csms.session.commands import SessionCommands
from ocpp_csms.cli.charger_diagnostics import add_diagnostics_command
from ocpp_csms import diagnostic_upload


class FakeSession(SessionCommands):
    def __init__(self):
        self._response_timeout = 30
        self.during = []
        self.records = []

    def _record(self, *args, **kwargs):
        self.records.append((args, kwargs))

    async def call(self, request):
        self.during.append(self._response_timeout)
        return SimpleNamespace(file_name="logs.zip")


@pytest.mark.asyncio
async def test_diagnostics_default_wait_and_restores_timeout():
    session = FakeSession()
    response = await session.get_diagnostics("https://example.com/private-token", response_timeout=None)
    assert response.file_name == "logs.zip"
    assert session.during == [10**9]
    assert session._response_timeout == 30
    assert "private-token" not in repr(session.records)


@pytest.mark.asyncio
async def test_diagnostics_explicit_limit_and_restores_after_error():
    session = FakeSession()
    await session.get_diagnostics("https://example.com/secret", response_timeout=12)
    assert session.during == [12]
    assert session._response_timeout == 30

    async def failing(request):
        raise TimeoutError("payload with sensitive URL")
    session.call = failing
    with pytest.raises(TimeoutError):
        await session.get_diagnostics("https://example.com/secret", response_timeout=None)
    assert session._response_timeout == 30


def test_timeout_record_preserved(tmp_path, monkeypatch):
    root = tmp_path / "uploads"
    root.mkdir()
    cfg = tmp_path / "config.json"
    cfg.write_text('{"hostname":"example.com"}')
    monkeypatch.setattr(diagnostic_upload, "ROOT", root)
    monkeypatch.setattr(diagnostic_upload, "CONFIG", cfg)
    record, _ = diagnostic_upload.prepare(tmp_path, "charger-1")
    diagnostic_upload.mark_timeout(tmp_path, record["id"])
    results = diagnostic_upload.list_requests(tmp_path, charger="charger-1")
    assert results[0]["result"] == "response_unconfirmed"

def test_request_parser_follow_limit():
    from ocpp_csms.cli import build_parser
    parser, _ = build_parser()
    args = parser.parse_args(["diagnose", "request", "--follow", "--limit", "90"])
    assert args.follow is True
    assert args.limit == 90
    assert parser.parse_args(["diagnose", "request"]).limit is None


def test_independent_upload_notification_without_callresult(tmp_path, monkeypatch, capsys):
    from ocpp_csms.cli import charger_diagnostics
    from types import SimpleNamespace
    root = tmp_path / "uploads"
    root.mkdir()
    cfg = tmp_path / "config.json"
    cfg.write_text('{"hostname":"example.com"}')
    monkeypatch.setattr(diagnostic_upload, "ROOT", root)
    monkeypatch.setattr(diagnostic_upload, "CONFIG", cfg)
    monkeypatch.setattr(diagnostic_upload, "_notifications", lambda *args: [{"at":"2026-10-22T03:41:47Z","status":"Uploading"}])
    def missing_result(*args, **kwargs):
        raise AssertionError("CALLRESULT should not be necessary")
    # The dispatch worker may continue independently; this assertion only
    # needs the Uploading evidence, not a synchronous control response.
    monkeypatch.setattr(charger_diagnostics, "maintenance_control", lambda *args, **kwargs: __import__("time").sleep(1))
    args = SimpleNamespace(diagnostic_operation="request", limit=3, follow=False, charger="charger-1",
        data_dir=tmp_path, since=None, until=None, retries=None, retry_interval=None, json=False)
    assert charger_diagnostics.run_https_diagnostics(args) == 0
    assert "Uploading" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_concurrent_diagnostics_and_control_do_not_share_timeout():
    """Each outgoing CALL observes its own timeout even while another CALL waits."""
    class WaitingSession(FakeSession):
        def __init__(self):
            super().__init__()
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def call(self, request):
            self.during.append(self._response_timeout)
            if len(self.during) == 1:
                self.entered.set()
                await self.release.wait()
            return SimpleNamespace(file_name="logs.zip", status="Accepted")

    session = WaitingSession()
    first = asyncio.create_task(
        session.get_diagnostics("https://example.com/private", response_timeout=12)
    )
    await asyncio.wait_for(session.entered.wait(), timeout=2)
    second = asyncio.create_task(
        session.get_diagnostics("https://example.com/other", response_timeout=19)
    )
    control = asyncio.create_task(session.remote_stop(42))
    try:
        await asyncio.sleep(0)
        assert session._response_timeout == 12
        assert session.during == [12]
    finally:
        session.release.set()
        await asyncio.gather(first, second, control, return_exceptions=True)
    assert session.during == [12, 19, 30]
    assert session._response_timeout == 30


@pytest.mark.asyncio
async def test_cancelled_diagnostics_restores_default_timeout():
    class WaitingSession(FakeSession):
        def __init__(self):
            super().__init__()
            self.entered = asyncio.Event()

        async def call(self, request):
            self.entered.set()
            await asyncio.Event().wait()

    session = WaitingSession()
    pending = asyncio.create_task(
        session.get_diagnostics("https://example.com/private", response_timeout=11)
    )
    await asyncio.wait_for(session.entered.wait(), timeout=2)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert session._response_timeout == 30
