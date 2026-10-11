"""Firmware preflight never performs UpdateFirmware or other mutating operations."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from ocpp_csms.firmware_preflight import preflight, validate_location


@pytest.mark.parametrize("url,valid", [
    ("https://example.org/fw.bin", True), ("ftp://example.org/fw", True),
    ("file:///tmp/fw", False), ("not-a-url", False),
    ("https://example.org/fw#fragment", False),
])
def test_location_validation(url, valid):
    assert bool(validate_location(url)) is valid


@pytest.mark.asyncio
async def test_offline_preflight_has_no_control_calls(monkeypatch):
    import ocpp_csms.firmware_preflight as module
    send = AsyncMock(side_effect=AssertionError("should not query charger"))
    monkeypatch.setattr(module, "send_control", send)
    cp = SimpleNamespace(charger_id="CP1", connected=False, connected_at=None, last_seen=None)
    monkeypatch.setattr(module, "appliance_status", lambda _: {"chargers": [cp]})
    monkeypatch.setattr(module, "_reconciliation", lambda *args: [])
    monkeypatch.setattr(module, "_last_observation", lambda *args: None)
    class FakeQuery:
        def __init__(self, _): pass
        def active(self, **kwargs): return []
    monkeypatch.setattr(module, "TransactionQuery", FakeQuery)
    report = await preflight("/unused", "CP1", location="https://host/fw.bin", query_capability=False)
    assert report["compatibility"] == "unverified"
    assert report["assessment"] == "Warnings"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_open_transaction_and_invalid_url_are_reported(monkeypatch):
    import ocpp_csms.firmware_preflight as module
    cp = SimpleNamespace(charger_id="CP1", connected=True, connected_at="2026-10-10T12:00:00Z", last_seen=None)
    monkeypatch.setattr(module, "appliance_status", lambda _: {"chargers": [cp]})
    monkeypatch.setattr(module, "_reconciliation", lambda *args: [{"assessment": "Conflict"}])
    monkeypatch.setattr(module, "_last_observation", lambda *args: None)
    class FakeQuery:
        def __init__(self, _): pass
        def active(self, **kwargs): return [SimpleNamespace(transaction_id=42)]
    monkeypatch.setattr(module, "TransactionQuery", FakeQuery)
    report = await preflight("/unused", "CP1", location="file:///tmp/fw", query_capability=False)
    assert report["assessment"] == "Blocked"
    assert next(f for f in report["findings"] if f["check"] == "transactions")["state"] == "warning"
    assert next(f for f in report["findings"] if f["check"] == "location")["state"] == "error"


@pytest.mark.asyncio
async def test_live_check_only_queries_feature_profile(monkeypatch):
    import ocpp_csms.firmware_preflight as module
    cp = SimpleNamespace(charger_id="CP1", connected=True, connected_at=None, last_seen=None)
    monkeypatch.setattr(module, "appliance_status", lambda _: {"chargers": [cp]})
    monkeypatch.setattr(module, "_reconciliation", lambda *args: [])
    monkeypatch.setattr(module, "_last_observation", lambda *args: None)
    class FakeQuery:
        def __init__(self, _): pass
        def active(self, **kwargs): return []
    monkeypatch.setattr(module, "TransactionQuery", FakeQuery)
    send = AsyncMock(return_value={"response": {"configuration_key": [
        {"key": "SupportedFeatureProfiles", "value": "Core,FirmwareManagement", "readonly": True}
    ]}})
    monkeypatch.setattr(module, "send_control", send)
    report = await preflight("/unused", "CP1")
    request = send.await_args.args[1]
    assert request["command"] == "config"
    assert request["keys"] == ["SupportedFeatureProfiles"]
    assert next(f for f in report["findings"] if f["check"] == "capability")["state"] == "ok"


def test_firmware_check_parser(parse_cli):
    args = parse_cli("firmware", "check", "--cp", "CP1", "--offline", "--json")
    assert args.firmware_action == "check" and args.charger == "CP1" and args.offline and args.json
