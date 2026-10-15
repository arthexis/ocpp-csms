"""Firmware CLI and OCPP dispatch safety contracts."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from ocpp_csms.cli.firmware import add_firmware_command, firmware_request
from ocpp_csms.control.dispatch import dispatch_control

NOW = datetime(2026, 10, 10, 20, 0, tzinfo=timezone.utc)

def test_firmware_retrieval_date_contract(parse_cli):
    args = parse_cli("firmware", "update", "--location", "https://example.org/fw.bin",
                     "--confirm")
    request = firmware_request(args, now=NOW)
    assert request["retrieve_date"] == NOW.isoformat()
    assert "immediate" not in request


@pytest.mark.parametrize("args", [
    (),
])
def test_confirmation_required(args, parse_cli):
    with pytest.raises(ValueError, match="confirm"):
        firmware_request(parse_cli("firmware", "update", "--location", "https://example.org/fw.bin", *args), now=NOW)

@pytest.mark.parametrize("url", ["file:///tmp/a", "not-a-url", "https://", "https://host/a#frag"])
def test_invalid_url(url, parse_cli):
    with pytest.raises(ValueError):
        firmware_request(parse_cli("firmware", "update", "--location", url, "--confirm"), now=NOW)

@pytest.mark.asyncio
async def test_dispatch_empty_acknowledgment():
    session = SimpleNamespace(update_firmware=AsyncMock(return_value=SimpleNamespace()))
    registry = SimpleNamespace(connected_chargers=lambda: ["CP1"], session=lambda _: session)
    data = {"command": "update_firmware", "location": "https://example.org/fw",
            "retrieve_date": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            }
    result = await dispatch_control(registry, data)
    assert result == {"ok": True, "response": {}}
    session.update_firmware.assert_awaited_once()

@pytest.mark.asyncio
async def test_dispatch_rejects_bad_retries():
    registry = SimpleNamespace(connected_chargers=lambda: ["CP1"], session=lambda _: object())
    result = await dispatch_control(registry, {"command": "update_firmware",
            "location": "https://example.org/fw", "retrieve_date": NOW.isoformat(),
            "retries": -1})
    assert result["error"] == "invalid_retries"

