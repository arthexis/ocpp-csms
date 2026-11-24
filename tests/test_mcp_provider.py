import json

import pytest

from ocpp_csms import mcp_provider as provider
from ocpp_csms.status import ChargerStatus, ConnectorStatus


@pytest.fixture
def observation(monkeypatch):
    connector = ConnectorStatus(1, "Charging", "NoError", 42, "SECRET_RFID", None, info="SECRET_INFO")
    charger = ChargerStatus("CP1", True, None, "ocpp1.6", None, "Charging", "NoError", 42, "SECRET_RFID", None, [connector], info="SECRET_INFO")
    monkeypatch.setattr(provider, "read_status", lambda *_: {
        "database": "ok", "server": "stopped", "chargers": [charger],
    })


def test_catalog_is_read_only():
    assert {t["name"] for t in provider.tools()} == {"charger_list", "charger_status", "connector_list", "transaction_list", "event_list"}
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
    monkeypatch.setattr(provider, "read_status", lambda *_: pytest.fail("unexpected query"))
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
    assert len(result["result"]["tools"]) == 5
    result = provider.dispatch(tmp_path, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "reset"}})
    assert result["result"]["isError"]

@pytest.fixture
def evidence(tmp_path):
    from ocpp_csms.evidence.store import EventStore
    store = EventStore(tmp_path)
    store.record_runtime("charger_connected", charger_id="CP1", details={"secret": "SECRET_RUNTIME"})
    store.record_runtime("charger_connected", charger_id="CP2")
    payload = {"connector_id": 1, "status": "Charging", "error_code": "NoError", "info": "SECRET_INFO"}
    store.record_connector_status("CP1", payload)
    store.record_ocpp("CP1", "StatusNotification", payload)
    for index in range(3):
        store.commit_start("CP1", {"connector_id": index + 1, "id_tag": "SECRET_RFID",
                                   "meter_start": 1000, "timestamp": "2026-10-01T15:00:00Z"})
    return tmp_path


def test_real_sqlite_and_cli_parity(evidence):
    from ocpp_csms.status import appliance_status
    from ocpp_csms.status_contract import status_contract
    cli = status_contract(appliance_status(evidence), charger_id="CP1")["data"]["chargers"][0]
    result = provider.invoke(evidence, "charger_status", {"charge_point_id": "CP1"})
    assert result["charger"]["status"] == cli["status"]
    assert result["connectors"][0]["connector_id"] == cli["connectors"][0]["id"]
    assert "SECRET" not in json.dumps(result)
    assert result["charger"]["connected"] is None
    assert len(provider.invoke(evidence, "charger_list", {})["chargers"]) == 2


@pytest.mark.parametrize("name,key", [("transaction_list", "transaction_id"), ("event_list", "id")])
def test_pagination_and_redaction(evidence, name, key):
    first = provider.invoke(evidence, name, {"charge_point_id": "CP1", "limit": 1})
    assert first["next_before_id"] == first["items"][0][key]
    second = provider.invoke(evidence, name, {"charge_point_id": "CP1", "limit": 1,
                                             "before_id": first["next_before_id"]})
    assert second["items"][0][key] < first["items"][0][key]
    assert "SECRET" not in json.dumps([first, second])
    assert "payload" not in json.dumps(first)


def test_read_only_connection_and_schema(evidence):
    import sqlite3
    from ocpp_csms.evidence.store import DATABASE_FILENAME
    with provider._evidence(evidence) as db:
        with pytest.raises(sqlite3.OperationalError):
            db.execute("DELETE FROM events")
    with sqlite3.connect(evidence / DATABASE_FILENAME) as db:
        db.execute("PRAGMA user_version=1")
    with pytest.raises(provider.ToolError, match="schema_mismatch"):
        provider.read_status(evidence)


def test_limits(evidence, monkeypatch):
    monkeypatch.setattr(provider, "MAX_ITEMS", 1)
    with pytest.raises(provider.ToolError, match="result_limit_exceeded"):
        provider.read_status(evidence)
    assert provider.read_status(evidence, "CP2")["chargers"][0].charger_id == "CP2"
    monkeypatch.setattr(provider, "MAX_STATUS_ROWS", 1)
    with pytest.raises(provider.ToolError, match="result_limit_exceeded"):
        provider.read_status(evidence, "CP1")


@pytest.mark.parametrize("args", [{"limit": True}, {"limit": 0}, {"limit": 101}, {"before_id": None}, {"before_id": 2**63}, {"node": "other"}])
def test_paging_rejects_invalid_input(evidence, args):
    with pytest.raises(provider.ToolError, match="invalid_arguments"):
        provider.invoke(evidence, "event_list", {"charge_point_id": "CP1", **args})


def test_stdio_protocol(evidence):
    import subprocess
    import sys
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": provider.PROTOCOL}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "event_list", "arguments": {"charge_point_id": "CP1"}}},
    ]
    proc = subprocess.run([sys.executable, "-m", "ocpp_csms.mcp_provider", "--data-dir", str(evidence)],
                          input="".join(json.dumps(msg) + "\n" for msg in messages),
                          text=True, capture_output=True, timeout=10, check=True)
    responses = [json.loads(line) for line in proc.stdout.splitlines()]
    assert [r["id"] for r in responses] == [1, 2, 3]
    assert len(responses[1]["result"]["tools"]) == 5
    assert not responses[2]["result"]["isError"]
    assert "SECRET" not in proc.stdout
    assert not proc.stderr


def test_oversized_stdio_frame(evidence):
    import subprocess
    import sys
    proc = subprocess.run([sys.executable, "-m", "ocpp_csms.mcp_provider", "--data-dir", str(evidence)],
                          input=b"x" * (provider.MAX_MESSAGE + 1), capture_output=True, timeout=10)
    assert proc.returncode == 1
    assert not proc.stdout
