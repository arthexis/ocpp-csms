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


@pytest.mark.parametrize(("connected", "status", "ids", "expected"), [
    (True, "Available", [42], "Conflict"),
    (True, "Charging", [42, 43], "Conflict (overlapping transactions)"),
    (True, "Available", [42, 43], "Conflict (overlapping transactions)"),
    (False, "Available", [42], "Offline/uncertain"),
    (True, None, [42], "Unknown"),
    (True, "Charging", [42], "Consistent (snapshot)"),
])
def test_reconcile_classifies_transaction_evidence(connected, status, ids, expected):
    from ocpp_csms.cli.inspection import _assessment
    assert _assessment(connected=connected, status=status, ids=ids) == expected


def test_reconcile_honors_cp_filter_for_missing_connector_observations(monkeypatch):
    from ocpp_csms.cli import inspection
    from types import SimpleNamespace
    monkeypatch.setattr(inspection, "appliance_status", lambda _: {"chargers": []})
    transactions = [
        SimpleNamespace(charge_point_id="CP1", connector_id=1, transaction_id=42),
        SimpleNamespace(charge_point_id="CP2", connector_id=1, transaction_id=43),
    ]
    class FakeQuery:
        def __init__(self, _):
            pass

        def active(self, *, charger, connector):
            return transactions  # deliberately unfiltered to verify the output guard

    monkeypatch.setattr(inspection, "TransactionQuery", FakeQuery)
    rows = inspection._reconciliation("/unused", "CP1", None)
    assert len(rows) == 1
    assert rows[0]["cp"] == "CP1"
    assert rows[0]["assessment"] == "No connector observation"
