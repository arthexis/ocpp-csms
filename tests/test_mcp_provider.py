import json

import pytest

from ocpp_csms import mcp_provider as provider
from ocpp_csms.status import ChargerStatus, ConnectorStatus


@pytest.fixture
def observation(monkeypatch):
    connector = ConnectorStatus(1, "Charging", "NoError", 42, "SECRET_RFID", None, info="SECRET_INFO")
    charger = ChargerStatus("CP1", True, None, "ocpp1.6", None, "Charging", "NoError", 42, "SECRET_RFID", None, [connector], info="SECRET_INFO")
    monkeypatch.setattr(provider, "read_status", lambda _: {
        "database": "ok", "server": "stopped", "chargers": [charger],
    })


def test_catalog_is_read_only():
    assert {t["name"] for t in provider.tools()} == {"charger_list", "charger_status", "connector_list"}
    assert all(t["annotations"]["readOnlyHint"] and not t["annotations"]["destructiveHint"] for t in provider.tools())


def test_stored_status_redacts_and_does_not_claim_live(tmp_path, observation):
    result = provider.invoke(tmp_path, "charger_status", {"charge_point_id": "CP1"})
    assert result["charger"]["connected"] is None
    assert result["connectors"][0]["transaction_id"] == 42
    assert "SECRET" not in json.dumps(result)
    assert "rfid" not in json.dumps(result)


def test_list_and_connectors(tmp_path, observation):
    assert provider.invoke(tmp_path, "charger_list", {})["chargers"][0]["charge_point_id"] == "CP1"
    assert provider.invoke(tmp_path, "connector_list", {"charge_point_id": "CP1"})["connectors"][0]["connector_id"] == 1


@pytest.mark.parametrize("name,args", [
    ("reset", {}), ("charger_list", {"data_dir": "/tmp"}),
    ("charger_status", {}), ("connector_list", {"charge_point_id": 1}),
])
def test_invalid_calls_do_not_read(tmp_path, monkeypatch, name, args):
    monkeypatch.setattr(provider, "read_status", lambda _: pytest.fail("unexpected query"))
    with pytest.raises(provider.ToolError):
        provider.invoke(tmp_path, name, args)


def test_unknown_target(tmp_path, observation):
    with pytest.raises(provider.ToolError, match="charge_point_not_found"):
        provider.invoke(tmp_path, "charger_status", {"charge_point_id": "OTHER"})


def test_missing_database_is_not_created(tmp_path):
    with pytest.raises(provider.ToolError, match="evidence_unavailable"):
        provider.read_status(tmp_path)
    assert not list(tmp_path.iterdir())


def test_json_rpc(tmp_path, observation):
    assert provider.dispatch(tmp_path, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    result = provider.dispatch(tmp_path, {"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert result["result"]["protocolVersion"] == "2025-03-26"
    result = provider.dispatch(tmp_path, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert len(result["result"]["tools"]) == 3
    result = provider.dispatch(tmp_path, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "reset"}})
    assert result["result"]["isError"]
