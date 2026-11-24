import asyncio
import json

import pytest

from ocpp_csms import mcp_commands as commands
from ocpp_csms import mcp_provider as provider


CP = {"charge_point_id": "CP1"}


def test_execute_catalog_is_opt_in_and_requires_execute_scope():
    assert len(provider.tools()) == 5
    tools = provider.tools(enable_execute=True)
    assert len(tools) == 14
    # Exact Watchtower mcp_tool_scope.required_scope classification contract.
    def scope(tool):
        a = tool.get("annotations", {})
        return "read" if a.get("readOnlyHint") is True and a.get("destructiveHint") is not True else "execute"
    assert sum(scope(t) == "read" for t in tools) == 5
    assert all(scope(t) == "execute" for t in tools if t["name"] in commands.SPECS)


def test_default_denies_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(commands, "execute", lambda *a: pytest.fail("unexpected dispatch"))
    with pytest.raises(provider.ToolError, match="execute_disabled"):
        provider.invoke(tmp_path, "reset", {**CP, "reset_type": "Soft"})


@pytest.mark.parametrize("name,args,expected", [
    ("remote_start", {"connector_id": 2, "id_tag": "AUTO"}, {"command": "start", "connector": 2, "id_tag": "AUTO"}),
    ("remote_stop", {"transaction_id": 1}, {"command": "stop", "transaction": 1}),
    ("reset", {"reset_type": "Hard"}, {"command": "reset", "type": "Hard"}),
    ("change_availability", {"connector_id": 0, "availability": "Inoperative"}, {"command": "availability", "connector": 0, "type": "Inoperative"}),
    ("unlock_connector", {"connector_id": 1}, {"command": "unlock", "connector": 1}),
    ("trigger_message", {"message": "MeterValues", "connector_id": 1}, {"command": "trigger", "message": "MeterValues", "connector": 1}),
    ("clear_cache", {}, {"command": "rfid_cache_clear"}),
    ("get_configuration", {"keys": ["HeartbeatInterval"]}, {"command": "config", "keys": ["HeartbeatInterval"]}),
    ("change_configuration", {"key": "HeartbeatInterval", "value": "60"}, {"command": "config_set", "key": "HeartbeatInterval", "value": "60"}),
])
def test_typed_request(name, args, expected):
    assert commands.build_request(name, {**CP, **args}) == {"charger": "CP1", **expected}


@pytest.mark.parametrize("name,args", [
    ("reset", {"reset_type": "Hard", "node": "other"}),
    ("remote_start", {"connector_id": 0, "id_tag": "AUTO"}),
    ("remote_start", {"connector_id": True, "id_tag": "AUTO"}),
    ("remote_start", {"connector_id": 1, "id_tag": "x" * 21}),
    ("trigger_message", {"message": "Heartbeat", "connector_id": 1}),
    ("get_configuration", {"keys": ["Password"]}),
    ("get_configuration", {"keys": []}),
    ("change_configuration", {"key": "Password", "value": "secret"}),
])
def test_invalid_never_dispatches(tmp_path, monkeypatch, name, args):
    monkeypatch.setattr(commands, "execute", lambda *a: pytest.fail("unexpected dispatch"))
    with pytest.raises(provider.ToolError, match="invalid_arguments"):
        provider.invoke(tmp_path, name, {**CP, **args}, enable_execute=True)


@pytest.mark.parametrize("reply,outcome", [
    ({"ok": True, "response": {"status": "Accepted"}}, "acknowledged"),
    ({"ok": True, "response": {"status": "Rejected"}}, "rejected"),
    ({"error": "charger_not_connected"}, "not_dispatched"),
    ({"error": "command_failed", "detail": "SECRET"}, "delivery_unknown"),
    ({"ok": True, "response": {"status": "unexpected"}}, "delivery_unknown"),
    ({"ok": True, "response": {"status": []}}, "delivery_unknown"),
])
def test_single_dispatch_and_outcome(tmp_path, monkeypatch, reply, outcome):
    calls = []
    async def send(data_dir, request):
        calls.append(request)
        return reply
    monkeypatch.setattr(commands, "send_control", send)
    result = provider.invoke(tmp_path, "reset", {**CP, "reset_type": "Soft"}, enable_execute=True)
    assert len(calls) == 1
    assert result["outcome"] == outcome
    assert result["physical_outcome"] == "not_verified"
    assert result["automatic_retry"] is False
    assert "SECRET" not in json.dumps(result)


def test_timeout_no_retry(tmp_path, monkeypatch):
    calls = []
    async def send(*args):
        calls.append(args)
        await asyncio.sleep(1)
    monkeypatch.setattr(commands, "send_control", send)
    monkeypatch.setattr(commands, "TIMEOUT", 0.01)
    result = provider.invoke(tmp_path, "clear_cache", CP, enable_execute=True)
    assert result["outcome"] == "delivery_unknown"
    assert len(calls) == 1


def test_stop_wrong_charger(tmp_path, monkeypatch):
    from ocpp_csms.evidence.store import EventStore
    store = EventStore(tmp_path)
    tx = store.commit_start("CP2", {"connector_id": 1, "id_tag": "AUTO", "meter_start": 0,
                                    "timestamp": "2026-10-01T15:00:00Z"})
    monkeypatch.setattr(commands, "execute", lambda *a: pytest.fail("unexpected dispatch"))
    with pytest.raises(provider.ToolError, match="transaction_target_mismatch"):
        provider.invoke(tmp_path, "remote_stop", {**CP, "transaction_id": tx}, enable_execute=True)


def test_configuration_readback_filters_unrequested_keys():
    args = {**CP, "key": "HeartbeatInterval", "value": "60"}
    result = commands.interpret("change_configuration", args, {"ok": True, "response": {
        "change": {"status": "RebootRequired"},
        "readback": {"configuration_key": [
            {"key": "HeartbeatInterval", "value": "30", "readonly": False},
            {"key": "Password", "value": "SECRET", "readonly": False},
        ]},
    }})
    assert result["outcome"] == "reboot_required"
    assert result["readback"]["configuration"][0]["value"] == "30"
    assert "SECRET" not in json.dumps(result)


@pytest.mark.asyncio
async def test_real_unix_transport_without_charger(tmp_path):
    received = []
    async def handler(reader, writer):
        received.append(json.loads(await reader.readline()))
        writer.write(b'{"ok":true,"response":{"status":"Accepted"}}\n')
        await writer.drain()
        writer.close()
        await writer.wait_closed()
    server = await asyncio.start_unix_server(handler, path=str(tmp_path / "control.sock"))
    async with server:
        req = commands.build_request("reset", {**CP, "reset_type": "Soft"})
        reply = await commands.send_once(tmp_path, req)
    assert received == [req]
    assert commands.interpret("reset", CP, reply)["outcome"] == "acknowledged"


def test_process_memory_limit():
    import subprocess
    import sys
    code = ("from ocpp_csms.mcp_provider import _bound_memory; "
            "import resource; _bound_memory(); "
            "print(resource.getrlimit(resource.RLIMIT_AS)[0])")
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, check=True, timeout=10)
    assert 0 < int(proc.stdout.strip()) <= 256 * 1024 * 1024


def test_missing_socket_returns_uncertainty(tmp_path):
    result = provider.invoke(tmp_path, "reset", {**CP, "reset_type": "Soft"}, enable_execute=True)
    assert result["outcome"] == "delivery_unknown"
    assert result["automatic_retry"] is False
