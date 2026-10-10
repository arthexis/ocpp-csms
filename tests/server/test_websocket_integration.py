import json

import pytest
import websockets

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.server import CSMSServer, OCPP_16_SUBPROTOCOL, charge_point_id_from_path
from ocpp_csms.transactions import TransactionArchive


def call(unique_id, action, payload):
    return json.dumps([2, unique_id, action, payload])


async def result(websocket, unique_id):
    message_type, response_id, payload = json.loads(await websocket.recv())
    assert message_type == 3
    assert response_id == unique_id
    return payload


def test_charge_point_id_uses_final_path_segment():
    assert charge_point_id_from_path("/charger-a") == "charger-a"
    assert charge_point_id_from_path("/ocpp/charger-a") == "charger-a"
    assert charge_point_id_from_path("/ocpp/charger-a/") == "charger-a"
    assert charge_point_id_from_path("/ocpp/charger-a?token=test") == "charger-a"
    assert charge_point_id_from_path("/") == ""


@pytest.mark.asyncio
async def test_real_websocket_boot_and_charging_flow(tmp_path):
    server = CSMSServer(
        host="127.0.0.1",
        port=0,
        transactions=TransactionArchive(tmp_path),
        events=EventStore(tmp_path),
    )

    async with websockets.serve(
        server.accept,
        "127.0.0.1",
        0,
        subprotocols=[OCPP_16_SUBPROTOCOL],
    ) as websocket_server:
        port = websocket_server.sockets[0].getsockname()[1]
        async with websockets.connect(
            f"ws://127.0.0.1:{port}/ocpp/charger-a",
            subprotocols=[OCPP_16_SUBPROTOCOL],
        ) as websocket:
            assert websocket.subprotocol == OCPP_16_SUBPROTOCOL

            await websocket.send(
                call(
                    "boot-1",
                    "BootNotification",
                    {
                        "chargePointVendor": "Field Vendor",
                        "chargePointModel": "Field Model",
                    },
                )
            )
            boot = await result(websocket, "boot-1")
            assert boot["status"] == "Accepted"
            assert boot["interval"] == 60
            assert "currentTime" in boot

            await websocket.send(call("auth-1", "Authorize", {"idTag": "card-a"}))
            authorize = await result(websocket, "auth-1")
            assert authorize["idTagInfo"]["status"] == "Accepted"

            await websocket.send(
                call(
                    "start-1",
                    "StartTransaction",
                    {
                        "connectorId": 1,
                        "idTag": "card-a",
                        "meterStart": 100,
                        "timestamp": "2026-10-02T12:00:00Z",
                    },
                )
            )
            start = await result(websocket, "start-1")
            transaction_id = start["transactionId"]
            assert transaction_id > 0
            assert start["idTagInfo"]["status"] == "Accepted"

            await websocket.send(
                call(
                    "meter-1",
                    "MeterValues",
                    {
                        "connectorId": 1,
                        "transactionId": transaction_id,
                        "meterValue": [
                            {
                                "timestamp": "2026-10-02T12:01:00Z",
                                "sampledValue": [{"value": "120"}],
                            }
                        ],
                    },
                )
            )
            assert await result(websocket, "meter-1") == {}

            await websocket.send(
                call(
                    "stop-1",
                    "StopTransaction",
                    {
                        "meterStop": 130,
                        "timestamp": "2026-10-02T12:02:00Z",
                        "transactionId": transaction_id,
                    },
                )
            )
            assert await result(websocket, "stop-1") == {}

    transaction_files = list((tmp_path / "transactions").glob("*/*.json"))
    assert len(transaction_files) == 1
    transaction = json.loads(transaction_files[0].read_text(encoding="utf-8"))
    assert transaction["transaction_id"] == transaction_id
    assert transaction["charge_point_id"] == "charger-a"
    assert transaction["status"] == "stopped"

    with server.events._connect() as connection:
        actions = connection.execute(
            "SELECT charger_id, action, direction FROM events ORDER BY id"
        ).fetchall()
    assert {row[0] for row in actions} == {"charger-a"}
    assert [(row[1], row[2]) for row in actions] == [
        ("BootNotification", "in"),
        ("BootNotification", "out"),
        ("Authorize", "in"),
        ("Authorize", "out"),
        ("StartTransaction", "in"),
        ("StartTransaction", "out"),
        ("MeterValues", "in"),
        ("MeterValues", "out"),
        ("StopTransaction", "in"),
        ("StopTransaction", "out"),
    ]
