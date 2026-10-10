"""Vendor DataTransfer CLI, dispatch and unsolicited message tests."""
import argparse
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ocpp_csms.cli.data_transfer import add_data_transfer_command, transfer_request
from ocpp_csms.control.dispatch import dispatch_control
from ocpp_csms.session.handlers import SessionHandlers


def parse(*argv):
    parser = argparse.ArgumentParser()
    add_data_transfer_command(parser.add_subparsers(dest="command"))
    return parser.parse_args(["data-transfer", "send", *argv])


def test_cli_preserves_opaque_data():
    request = transfer_request(parse("--cp", "CP1", "--vendor", "com.example",
                                     "--message-id", "test", "--data", '{"answer":42}'))
    assert request == {"command": "data_transfer", "charger": "CP1",
                       "vendor_id": "com.example", "message_id": "test",
                       "data": '{"answer":42}'}


@pytest.mark.asyncio
async def test_outbound_dispatch():
    session = SimpleNamespace(data_transfer=AsyncMock(return_value=SimpleNamespace(status="Accepted", data="ok")))
    registry = SimpleNamespace(connected_chargers=lambda: ["CP1"], session=lambda _: session)
    result = await dispatch_control(registry, {"command": "data_transfer", "vendor_id": "com.example", "data": "hello"})
    assert result["response"]["status"] == "Accepted"
    session.data_transfer.assert_awaited_once_with("com.example", None, "hello")


@pytest.mark.asyncio
async def test_unknown_inbound_vendor_is_recorded_but_not_executed():
    recorded = []
    session = SimpleNamespace(_record=lambda action, payload: recorded.append((action, payload)))
    response = await SessionHandlers.on_data_transfer(session, vendor_id="other.vendor",
                                                       message_id="action", data="payload")
    assert response.status == "UnknownVendorId"
    assert recorded == [("DataTransfer", {"vendor_id": "other.vendor", "message_id": "action", "data": "payload"})]


@pytest.mark.asyncio
@pytest.mark.parametrize(("fields", "expected"), [
    ({"vendor_id": ""}, "invalid_vendor_id"),
    ({"vendor_id": "x" * 256}, "invalid_vendor_id"),
    ({"vendor_id": "ok", "message_id": "x" * 51}, "invalid_message_id"),
    ({"vendor_id": "ok", "data": {"json": "object"}}, "invalid_data"),
])
async def test_invalid_control_payload(fields, expected):
    registry = SimpleNamespace(connected_chargers=lambda: ["CP1"], session=lambda _: object())
    result = await dispatch_control(registry, {"command": "data_transfer", **fields})
    assert result["error"] == expected
