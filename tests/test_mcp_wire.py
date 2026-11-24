"""Wire-level charger simulator for the MCP execution surface (loopback only)."""
import asyncio
import json

import pytest
import websockets

from ocpp_csms.control import ControlServer
from ocpp_csms.evidence.store import EventStore
from ocpp_csms.mcp_provider import invoke
from ocpp_csms.server import CSMSServer
from ocpp_csms.transactions.archive import TransactionArchive


@pytest.mark.asyncio
async def test_remaining_commands_and_missing_callresult(tmp_path):
    server = CSMSServer("127.0.0.1", 0, TransactionArchive(tmp_path), EventStore(tmp_path))
    cp = {"charge_point_id": "WIRE394"}
    async with ControlServer(server, tmp_path / "control.sock"):
        async with websockets.serve(server.accept, "127.0.0.1", 0, subprotocols=["ocpp1.6"]) as ws:
            port = ws.sockets[0].getsockname()[1]
            async with websockets.connect(f"ws://127.0.0.1:{port}/WIRE394", subprotocols=["ocpp1.6"]) as peer:
                await peer.send(json.dumps([2, "boot", "BootNotification", {"chargePointVendor": "Fixture", "chargePointModel": "Fixture"}]))
                assert json.loads(await peer.recv())[2]["status"] == "Accepted"

                async def exchange(tool, args, action, payload, reply, error=False, second=None):
                    task = asyncio.create_task(asyncio.to_thread(invoke, tmp_path, tool, {**cp, **args}, enable_execute=True))
                    try:
                        msg = json.loads(await asyncio.wait_for(peer.recv(), 3))
                        assert msg[0] == 2 and msg[2:] == [action, payload]
                        await peer.send(json.dumps([4, msg[1], "NotImplemented", "fixture", {}] if error else [3, msg[1], reply]))
                        if second is not None:
                            msg = json.loads(await asyncio.wait_for(peer.recv(), 3))
                            assert msg[2] == "GetConfiguration"
                            await peer.send(json.dumps([4, msg[1], "NotImplemented", "fixture", {}] if second == "error" else [3, msg[1], second]))
                        return await asyncio.wait_for(task, 8)
                    finally:
                        if not task.done():
                            task.cancel()

                cases = [
                    ("reset", {"reset_type": "Soft"}, "Reset", {"type": "Soft"}, {"status": "Accepted"}, "acknowledged"),
                    ("change_availability", {"connector_id": 1, "availability": "Inoperative"}, "ChangeAvailability", {"connectorId": 1, "type": "Inoperative"}, {"status": "Scheduled"}, "scheduled"),
                    ("unlock_connector", {"connector_id": 1}, "UnlockConnector", {"connectorId": 1}, {"status": "Unlocked"}, "acknowledged"),
                    ("trigger_message", {"message": "Heartbeat"}, "TriggerMessage", {"requestedMessage": "Heartbeat"}, {"status": "Accepted"}, "acknowledged"),
                    ("clear_cache", {}, "ClearCache", {}, {"status": "Rejected"}, "rejected"),
                    ("clear_cache", {}, "ClearCache", {}, {"status": "Accepted"}, "acknowledged"),
                ]
                for tool, args, action, payload, reply, expected in cases:
                    result = await exchange(tool, args, action, payload, reply)
                    assert result["outcome"] == expected
                    assert result["physical_outcome"] == "not_verified" and not result["automatic_retry"]
                args = {"key": "HeartbeatInterval", "value": "30"}
                payload = {"key": "HeartbeatInterval", "value": "30"}
                readback = {"configurationKey": [{"key": "HeartbeatInterval", "readonly": False, "value": "30"}]}
                result = await exchange("change_configuration", args, "ChangeConfiguration", payload, {"status": "Accepted"}, second=readback)
                assert result["readback"]["configuration"][0]["value"] == "30"
                result = await exchange("change_configuration", args, "ChangeConfiguration", payload, {"status": "Accepted"}, second="error")
                assert result["outcome"] == "acknowledged" and result["readback"] is None
                assert result["readback_error"] == "missing_ocpp_response"
                result = await exchange("trigger_message", {"message": "Heartbeat"}, "TriggerMessage", {"requestedMessage": "Heartbeat"}, {}, error=True)
                assert result["outcome"] == "delivery_unknown" and result["reason"] == "missing_ocpp_response"
                assert not result["automatic_retry"]
    with server.events._connect() as db:
        rows = db.execute("SELECT action, direction FROM events WHERE action='TriggerMessage' ORDER BY id").fetchall()
        assert [tuple(row) for row in rows] == [("TriggerMessage", "out"), ("TriggerMessage", "in"), ("TriggerMessage", "out")]
        assert db.execute("SELECT count(*) FROM runtime_events WHERE event='ocpp_response_missing'").fetchone()[0] == 2
