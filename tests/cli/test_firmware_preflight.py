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


@pytest.fixture
def preflight_evidence(monkeypatch):
    """One consistent charger/evidence harness for all preflight scenarios."""
    import ocpp_csms.firmware_preflight as module
    state = {"connected": True, "active": [], "conflicts": [], "observations": {}}
    cp = SimpleNamespace(charger_id="CP1", connected=True, connected_at=None, last_seen=None)
    monkeypatch.setattr(module, "appliance_status", lambda _: {
        "chargers": [SimpleNamespace(**{**vars(cp), "connected": state["connected"]})]
    })
    monkeypatch.setattr(module, "_reconciliation", lambda *args: state["conflicts"])
    monkeypatch.setattr(module, "_last_observation",
                        lambda _, __, action: state["observations"].get(action))
    class FakeQuery:
        def __init__(self, data_dir):
            pass
        def active(self, **kwargs):
            return [SimpleNamespace(transaction_id=tx) for tx in state["active"]]
    monkeypatch.setattr(module, "TransactionQuery", FakeQuery)
    send = AsyncMock(side_effect=AssertionError("unexpected charger query"))
    monkeypatch.setattr(module, "send_control", send)
    return state, send


@pytest.mark.asyncio
async def test_offline_preflight_has_no_control_calls(preflight_evidence):
    state, send = preflight_evidence
    state["connected"] = False
    report = await preflight("/unused", "CP1", location="https://host/fw.bin", query_capability=False)
    assert report["compatibility"] == "unverified"
    assert report["assessment"] == "Warnings"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_open_transaction_and_invalid_url_are_reported(preflight_evidence):
    state, send = preflight_evidence
    state["active"] = [42]
    state["conflicts"] = [{"assessment": "Conflict"}]
    report = await preflight("/unused", "CP1", location="file:///tmp/fw", query_capability=False)
    assert report["assessment"] == "Blocked"
    assert next(f for f in report["findings"] if f["check"] == "transactions")["state"] == "warning"
    assert next(f for f in report["findings"] if f["check"] == "location")["state"] == "error"
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_check_only_queries_feature_profile(preflight_evidence):
    state, send = preflight_evidence
    send.side_effect = None
    send.return_value = {"response": {"configuration_key": [
        {"key": "SupportedFeatureProfiles", "value": "Core,FirmwareManagement", "readonly": True}
    ]}}
    report = await preflight("/unused", "CP1")
    request = send.await_args.args[1]
    assert request == {"command": "config", "charger": "CP1",
                       "keys": ["SupportedFeatureProfiles"], "force": True}
    assert next(f for f in report["findings"] if f["check"] == "capability")["state"] == "ok"
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_offline_mode_skips_query_even_when_connected(preflight_evidence):
    _, send = preflight_evidence
    report = await preflight("/unused", "CP1", query_capability=False)
    assert next(f for f in report["findings"] if f["check"] == "capability")["state"] == "unknown"
    send.assert_not_awaited()


def test_firmware_check_parser(parse_cli):
    args = parse_cli("firmware", "check", "--cp", "CP1", "--offline", "--json")
    assert args.firmware_action == "check" and args.charger == "CP1" and args.offline and args.json
