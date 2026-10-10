"""Phase 1 field-maintenance command coverage."""
from __future__ import annotations

import argparse
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ocpp_csms.cli.maintenance import add_maintenance_commands, maintenance_request, run_maintenance
from ocpp_csms.control.dispatch import dispatch_control


@pytest.mark.parametrize(("argv", "expected"), [
    (["trigger", "heartbeat"], {"command": "trigger", "message": "Heartbeat"}),
    (["trigger", "status", "-c", "2"], {"command": "trigger", "message": "StatusNotification", "connector": 2}),
    (["availability", "disable", "-c", "2"], {"command": "availability", "type": "Inoperative", "connector": 2}),
    (["availability", "enable"], {"command": "availability", "type": "Operative", "connector": 0}),
    (["unlock", "-c", "1"], {"command": "unlock", "connector": 1}),
])
def test_request_shape(argv, expected):
    parser = argparse.ArgumentParser()
    subcommands = parser.add_subparsers(dest="command")
    add_maintenance_commands(subcommands)
    args = parser.parse_args(argv)
    assert maintenance_request(args) == expected


@pytest.mark.parametrize("argv", [
    ["unlock", "-c", "0"],
    ["trigger", "heartbeat", "-c", "1"],
    ["trigger", "meter", "-c", "-1"],
    ["availability", "disable", "-c", "-1"],
])
def test_reject_invalid_cli_request(argv):
    parser = argparse.ArgumentParser()
    subcommands = parser.add_subparsers(dest="command")
    add_maintenance_commands(subcommands)
    with pytest.raises(ValueError):
        maintenance_request(parser.parse_args(argv))


@pytest.mark.asyncio
@pytest.mark.parametrize(("command", "method", "args"), [
    ({"command": "trigger", "message": "Heartbeat"}, "trigger_message", ("Heartbeat", None)),
    ({"command": "availability", "type": "Inoperative", "connector": 0}, "change_availability", (0, "Inoperative")),
    ({"command": "unlock", "connector": 1}, "unlock_connector", (1,)),
])
async def test_dispatch(command, method, args):
    session = SimpleNamespace(
        trigger_message=AsyncMock(return_value=SimpleNamespace(status="Accepted")),
        change_availability=AsyncMock(return_value=SimpleNamespace(status="Accepted")),
        unlock_connector=AsyncMock(return_value=SimpleNamespace(status="Accepted")),
    )
    registry = SimpleNamespace(connected_chargers=lambda: ["CP1"], session=lambda _: session)
    result = await dispatch_control(registry, command)
    assert result == {"ok": True, "response": {"status": "Accepted"}}
    getattr(session, method).assert_awaited_once_with(*args)


@pytest.mark.asyncio
@pytest.mark.parametrize(("command", "error"), [
    ({"command": "trigger", "message": "Invalid"}, "invalid_trigger_message"),
    ({"command": "trigger", "message": "Heartbeat", "connector": 1}, "connector_not_applicable"),
    ({"command": "availability", "type": "Invalid"}, "invalid_availability_type"),
    ({"command": "unlock", "connector": 0}, "invalid_connector"),
])
async def test_dispatch_rejects_invalid_request(command, error):
    registry = SimpleNamespace(connected_chargers=lambda: ["CP1"], session=lambda _: object())
    assert (await dispatch_control(registry, command))["error"] == error


def test_scheduled_is_reported_as_pending(monkeypatch, capsys):
    async def send(*_args, **_kwargs):
        return {"ok": True, "response": {"status": "Scheduled"}}
    monkeypatch.setattr("ocpp_csms.cli.maintenance.send_control", send)
    args = SimpleNamespace(command="availability", charger=None, action="disable", connector=1, json=False, data_dir="/tmp")
    assert run_maintenance(args) == 0
    assert "Pending" in capsys.readouterr().out
