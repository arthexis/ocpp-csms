"""Phase 2 command contract tests."""
import argparse
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ocpp_csms.cli.inspection import _features, _reconciliation, add_inspection_commands
from ocpp_csms.cli.rfid import add_rfid_command, _control_request
from ocpp_csms.control.dispatch import dispatch_control


def test_rfid_cache_clear_is_not_list_clear():
    parser = argparse.ArgumentParser()
    add_rfid_command(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["rfid", "cache", "clear", "--cp", "CP1"])
    request = _control_request(args)
    assert request["command"] == "rfid_cache"
    assert request["charger"] == "CP1"


@pytest.mark.asyncio
async def test_clear_cache_dispatch():
    session = SimpleNamespace(clear_cache=AsyncMock(return_value=SimpleNamespace(status="Accepted")))
    registry = SimpleNamespace(connected_chargers=lambda: ["CP1"], session=lambda _: session)
    result = await dispatch_control(registry, {"command": "rfid_cache_clear"})
    assert result["response"]["status"] == "Accepted"
    session.clear_cache.assert_awaited_once_with()


def test_capability_missing_key_is_unknown():
    assert all(item["status"] == "Unknown" for item in _features({"unknown_key": ["SupportedFeatureProfiles"]}))


def test_capability_profile_is_advertised_not_verified():
    result = _features({"configuration_key": [{"key": "SupportedFeatureProfiles", "value": "Core,SmartCharging"}]})
    assert next(item["status"] for item in result if item["feature"] == "Smart charging") == "Advertised"


def test_inspection_parser():
    parser = argparse.ArgumentParser()
    add_inspection_commands(parser.add_subparsers(dest="command"))
    assert parser.parse_args(["reconcile", "--cp", "CP1", "-c", "1"]).connector == 1
