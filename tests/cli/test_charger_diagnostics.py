"""GetDiagnostics CLI and dispatch contract tests."""
from __future__ import annotations

import argparse
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ocpp_csms.cli.charger_diagnostics import add_diagnostics_command, _request
from ocpp_csms.control.dispatch import dispatch_control


def parse(*argv):
    parser = argparse.ArgumentParser()
    add_diagnostics_command(parser.add_subparsers(dest="command"))
    return parser.parse_args(["diagnostics", *argv])


def test_request_shape():
    result = _request(parse("request", "--location", "https://u:pw@example.org/secret?token=123", "--cp", "CP1", "--retries", "2"))
    assert result["command"] == "get_diagnostics"
    assert result["charger"] == "CP1"
    assert result["retries"] == 2


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://", "not-a-url", "https://host/path#fragment"])
def test_reject_bad_url(url):
    with pytest.raises(ValueError):
        _request(parse("request", "--location", url))


@pytest.mark.asyncio
async def test_dispatch_sends_get_diagnostics():
    session = SimpleNamespace(get_diagnostics=AsyncMock(return_value=SimpleNamespace(file_name="diagnostics.log")))
    registry = SimpleNamespace(connected_chargers=lambda: ["CP1"], session=lambda _: session)
    response = await dispatch_control(registry, {"command": "get_diagnostics", "location": "https://example.org/upload"})
    assert response["response"]["file_name"] == "diagnostics.log"
    session.get_diagnostics.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(("request", "error"), [
    ({"location": "file:///tmp/a"}, "invalid_location"),
    ({"location": "https://example.org", "retries": -1}, "invalid_retries"),
    ({"location": "https://example.org", "retry_interval": -1}, "invalid_retry_interval"),
    ({"location": "https://example.org", "start_time": "2026-10-11T00:00:00Z", "stop_time": "2026-10-10T00:00:00Z"}, "invalid_time_window"),
])
async def test_dispatch_rejects_invalid(request, error):
    registry = SimpleNamespace(connected_chargers=lambda: ["CP1"], session=lambda _: object())
    result = await dispatch_control(registry, {"command": "get_diagnostics", **request})
    assert result["error"] == error
