"""Firmware CLI and OCPP dispatch safety contracts."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from ocpp_csms.cli.firmware import add_firmware_command, firmware_request
from ocpp_csms.control.dispatch import dispatch_control

NOW = datetime(2026, 10, 10, 20, 0, tzinfo=timezone.utc)

def test_immediate_request(parse_cli):
    data = firmware_request(parse_cli("firmware", "update", "--location", "https://example.org/fw.bin",
                                  "--now", "--confirm"), now=NOW)
    assert data["immediate"] is True
    assert data["retrieve_date"] == NOW.isoformat()

def test_scheduled_request(parse_cli):
    future = (NOW + timedelta(days=1)).isoformat()
    data = firmware_request(parse_cli("firmware", "update", "--location", "https://example.org/fw.bin",
                                  "--at", future, "--confirm"), now=NOW)
    assert data["retrieve_date"] == future

@pytest.mark.parametrize("args", [
    ("--now",),
    ("--at", "2026-10-11T12:00:00Z"),
])
def test_confirmation_required(args, parse_cli):
    with pytest.raises(ValueError, match="confirm"):
        firmware_request(parse_cli("firmware", "update", "--location", "https://example.org/fw.bin", *args), now=NOW)

@pytest.mark.parametrize("url", ["file:///tmp/a", "not-a-url", "https://", "https://host/a#frag"])
def test_invalid_url(url, parse_cli):
    with pytest.raises(ValueError):
        firmware_request(parse_cli("firmware", "update", "--location", url, "--now", "--confirm"), now=NOW)

def test_schedule_must_be_future(parse_cli):
    with pytest.raises(ValueError, match="future"):
        firmware_request(parse_cli("firmware", "update", "--location", "https://example.org/fw",
                               "--at", "2026-10-09T12:00:00Z", "--confirm"), now=NOW)

@pytest.mark.asyncio
async def test_dispatch_empty_acknowledgment():
    session = SimpleNamespace(update_firmware=AsyncMock(return_value=SimpleNamespace()))
    registry = SimpleNamespace(connected_chargers=lambda: ["CP1"], session=lambda _: session)
    data = {"command": "update_firmware", "location": "https://example.org/fw",
            "retrieve_date": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "immediate": False}
    result = await dispatch_control(registry, data)
    assert result == {"ok": True, "response": {}}
    session.update_firmware.assert_awaited_once()

@pytest.mark.asyncio
async def test_dispatch_rejects_bad_retries():
    registry = SimpleNamespace(connected_chargers=lambda: ["CP1"], session=lambda _: object())
    result = await dispatch_control(registry, {"command": "update_firmware",
            "location": "https://example.org/fw", "retrieve_date": NOW.isoformat(),
            "retries": -1, "immediate": True})
    assert result["error"] == "invalid_retries"

def test_delayed_request(parse_cli):
    args = parse_cli("firmware", "update", "--location", "https://example.org/fw.bin",
                     "--after", "3600", "--confirm")
    data = firmware_request(args, now=NOW)
    assert data["retrieve_date"] == (NOW + timedelta(hours=1)).isoformat()
    assert data["immediate"] is False


@pytest.mark.parametrize("seconds", ("0", "-1"))
def test_delayed_request_requires_positive_seconds(parse_cli, seconds):
    args = parse_cli("firmware", "update", "--location", "https://example.org/fw.bin",
                     "--after", seconds, "--confirm")
    with pytest.raises(ValueError, match="--after"):
        firmware_request(args, now=NOW)


def test_firmware_update_requires_explicit_timing(parse_cli):
    with pytest.raises(SystemExit):
        parse_cli("firmware", "update", "--location", "https://example.org/fw.bin", "--confirm")


def test_firmware_update_rejects_multiple_timing_flags(parse_cli):
    with pytest.raises(SystemExit):
        parse_cli("firmware", "update", "--location", "https://example.org/fw.bin",
                  "--now", "--after", "3600", "--confirm")


def test_delayed_request_also_requires_confirmation(parse_cli):
    args = parse_cli("firmware", "update", "--location", "https://example.org/fw.bin", "--after", "60")
    with pytest.raises(ValueError, match="confirm"):
        firmware_request(args, now=NOW)
