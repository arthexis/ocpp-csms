"""Reservation command and dispatch contracts."""
import argparse
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from ocpp_csms.cli.reservation import add_reservation_commands, reservation_request
from ocpp_csms.control.dispatch import dispatch_control

def parse(*args):
    parser = argparse.ArgumentParser()
    add_reservation_commands(parser.add_subparsers(dest="command"))
    return parser.parse_args(args)

def test_reserve_shorthand_and_create_equivalent():
    until = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    flags = ("--cp", "CP1", "-c", "1", "--rfid", "ABC", "--until", until, "--id", "43")
    assert reservation_request(parse("reserve", *flags)) == reservation_request(parse("reservation", "create", *flags))

def test_cancel_request():
    assert reservation_request(parse("reservation", "cancel", "43", "--cp", "CP1")) == {
        "command": "cancel_reservation", "reservation_id": 43, "charger": "CP1"
    }

def test_invalid_connector():
    with pytest.raises(ValueError, match="connector"):
        reservation_request(parse("reserve", "-c", "-1", "--rfid", "ABC", "--until", "1d", "--id", "1"))

@pytest.mark.asyncio
async def test_reserve_dispatch_accepted():
    session = SimpleNamespace(reserve_now=AsyncMock(return_value=SimpleNamespace(status="Accepted")))
    registry = SimpleNamespace(connected_chargers=lambda: ["CP1"], session=lambda _: session)
    result = await dispatch_control(registry, {
        "command": "reserve", "connector": 1, "reservation_id": 42,
        "id_tag": "ABC", "expiry_date": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    })
    assert result["response"]["status"] == "Accepted"
    session.reserve_now.assert_awaited_once()

@pytest.mark.asyncio
async def test_cancel_dispatch():
    session = SimpleNamespace(cancel_reservation=AsyncMock(return_value=SimpleNamespace(status="Accepted")))
    registry = SimpleNamespace(connected_chargers=lambda: ["CP1"], session=lambda _: session)
    result = await dispatch_control(registry, {"command": "cancel_reservation", "reservation_id": 42})
    assert result["response"]["status"] == "Accepted"
    session.cancel_reservation.assert_awaited_once()

@pytest.mark.asyncio
async def test_invalid_reservation_id():
    registry = SimpleNamespace(connected_chargers=lambda: ["CP1"], session=lambda _: object())
    result = await dispatch_control(registry, {"command": "cancel_reservation", "reservation_id": 0})
    assert result["error"] == "invalid_reservation_id"
