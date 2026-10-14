import json

import pytest
import websockets

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.server import CSMSServer, OCPP_16_SUBPROTOCOL, charge_point_id_from_path
from ocpp_csms.transactions.archive import TransactionArchive
from ocpp_csms.transactions.query import TransactionQuery
from ocpp_csms.transactions.formatting import format_transactions
from ocpp_csms.export_contract import export_contract


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

    with server.events._connect() as connection:
        transaction = connection.execute(
            "SELECT transaction_id, charger_id, state, meter_stop FROM transactions WHERE transaction_id = ?",
            (transaction_id,),
        ).fetchone()
    assert transaction == (transaction_id, "charger-a", "stopped", 130)

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


@pytest.mark.asyncio
async def test_real_websocket_two_sessions_with_late_stop_and_export(tmp_path):
    """End-to-end: distinct starts, real meter evidence, late stop, and cursor."""
    server = CSMSServer(
        host="127.0.0.1",
        port=0,
        transactions=TransactionArchive(tmp_path),
        events=EventStore(tmp_path),
    )
    async with websockets.serve(
        server.accept, "127.0.0.1", 0, subprotocols=[OCPP_16_SUBPROTOCOL],
    ) as websocket_server:
        port = websocket_server.sockets[0].getsockname()[1]
        async with websockets.connect(
            f"ws://127.0.0.1:{port}/ocpp/charger-a",
            subprotocols=[OCPP_16_SUBPROTOCOL],
        ) as websocket:
            def start_payload(timestamp, meter_start):
                return {
                    "connectorId": 1,
                    "idTag": "card-a",
                    "meterStart": meter_start,
                    "timestamp": timestamp,
                }

            first_payload = start_payload("2026-10-02T12:00:00Z", 100)
            await websocket.send(call("start-a", "StartTransaction", first_payload))
            first = (await result(websocket, "start-a"))["transactionId"]

            await websocket.send(call("meter-a", "MeterValues", {
                "connectorId": 1,
                "transactionId": first,
                "meterValue": [{
                    "timestamp": "2026-10-02T12:01:00Z",
                    "sampledValue": [{
                        "value": "120",
                        "measurand": "Energy.Active.Import.Register",
                        "unit": "Wh",
                    }],
                }],
            }))
            assert await result(websocket, "meter-a") == {}

            second_payload = start_payload("2026-10-02T12:02:00Z", 200)
            await websocket.send(call("start-b", "StartTransaction", second_payload))
            second = (await result(websocket, "start-b"))["transactionId"]
            assert second > first

            before = TransactionQuery(tmp_path).get(first)
            assert before.effective_stop_source == "next_start"
            assert before.record["stop"] is None
            assert len(before.record["meter_values"]) == 1

            await websocket.send(call("stop-a", "StopTransaction", {
                "transactionId": first,
                "timestamp": "2026-10-02T12:03:00Z",
                "meterStop": 150,
            }))
            assert await result(websocket, "stop-a") == {}

            await websocket.send(call("stop-b", "StopTransaction", {
                "transactionId": second,
                "timestamp": "2026-10-02T12:04:00Z",
                "meterStop": 240,
            }))
            assert await result(websocket, "stop-b") == {}

    first_view = TransactionQuery(tmp_path).get(first)
    second_view = TransactionQuery(tmp_path).get(second)
    assert first_view.effective_stop_source == "observed"
    assert first_view.record["stop"]["meter_stop"] == 150
    assert second_view.record["stop"]["meter_stop"] == 240
    assert len(first_view.record["meter_values"]) == 1
    report = format_transactions([first_view, second_view])
    assert "0.050 kWh" in report
    assert "0.040 kWh" in report

    exported = export_contract(tmp_path, after=0, limit=100)["data"]
    incoming = [event for event in exported["events"] if event["direction"] == "in"]
    assert [event["action"] for event in incoming] == [
        "StartTransaction", "MeterValues", "StartTransaction",
        "StopTransaction", "StopTransaction",
    ]
    assert len({event["id"] for event in incoming}) == 5
    assert exported["cursor"]["next"] == exported["events"][-1]["id"]


@pytest.mark.asyncio
async def test_websocket_reconnect_and_server_restart_keep_distinct_session_identity(tmp_path):
    """A new server instance must retain IDs and preserve an incomplete first session."""
    payload_a = {
        "connectorId": 1, "idTag": "card-a", "meterStart": 100,
        "timestamp": "2026-10-02T12:00:00Z",
    }
    payload_b = {**payload_a, "meterStart": 200, "timestamp": "2026-10-02T12:05:00Z"}

    async def exchange(server, frames):
        responses = []
        async with websockets.serve(
            server.accept, "127.0.0.1", 0,
            subprotocols=[OCPP_16_SUBPROTOCOL],
        ) as listener:
            port = listener.sockets[0].getsockname()[1]
            async with websockets.connect(
                f"ws://127.0.0.1:{port}/ocpp/charger-a",
                subprotocols=[OCPP_16_SUBPROTOCOL],
            ) as websocket:
                for unique_id, action, payload in frames:
                    await websocket.send(call(unique_id, action, payload))
                    responses.append(await result(websocket, unique_id))
        return responses

    first_server = CSMSServer(
        host="127.0.0.1", port=0,
        transactions=TransactionArchive(tmp_path), events=EventStore(tmp_path),
    )
    [first] = await exchange(first_server, [
        ("start-a", "StartTransaction", payload_a),
    ])
    first_id = first["transactionId"]

    restarted = CSMSServer(
        host="127.0.0.1", port=0,
        transactions=TransactionArchive(tmp_path), events=EventStore(tmp_path),
    )
    retry, second, late_stop = await exchange(restarted, [
        ("retry-a", "StartTransaction", payload_a),
        ("start-b", "StartTransaction", payload_b),
        ("late-stop-a", "StopTransaction", {
            "transactionId": first_id, "meterStop": 150,
            "timestamp": "2026-10-02T12:06:00Z",
        }),
    ])
    assert retry["transactionId"] == first_id
    second_id = second["transactionId"]
    assert second_id > first_id
    assert late_stop == {}

    records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (tmp_path / "transactions").glob("*/*.json")
    ]
    assert len(records) == 2
    assert {record["transaction_id"] for record in records} == {first_id, second_id}
    assert TransactionQuery(tmp_path).get(first_id).effective_stop_source == "observed"
    assert TransactionQuery(tmp_path).get(second_id).record["stop"] is None

    exported = export_contract(tmp_path, after=0, limit=100)["data"]
    starts = [
        event for event in exported["events"]
        if event["direction"] == "in" and event["action"] == "StartTransaction"
    ]
    assert len(starts) == 3
    assert len({event["id"] for event in starts}) == 3
    assert [event["transaction_id"] for event in starts] == [
        first_id, first_id, second_id,
    ]


@pytest.mark.asyncio
async def test_websocket_orphan_stop_then_valid_start_does_not_adopt_foreign_id(tmp_path):
    """Orphan evidence is acknowledged without creating a session or consuming its ID."""
    server = CSMSServer(
        host="127.0.0.1", port=0,
        transactions=TransactionArchive(tmp_path), events=EventStore(tmp_path),
    )
    async with websockets.serve(
        server.accept, "127.0.0.1", 0,
        subprotocols=[OCPP_16_SUBPROTOCOL],
    ) as listener:
        port = listener.sockets[0].getsockname()[1]
        async with websockets.connect(
            f"ws://127.0.0.1:{port}/ocpp/charger-a",
            subprotocols=[OCPP_16_SUBPROTOCOL],
        ) as websocket:
            await websocket.send(call("orphan", "StopTransaction", {
                "transactionId": 900000, "meterStop": 900,
                "timestamp": "2026-10-02T12:00:00Z",
            }))
            assert await result(websocket, "orphan") == {}
            await websocket.send(call("start", "StartTransaction", {
                "connectorId": 1, "idTag": "card-a", "meterStart": 100,
                "timestamp": "2026-10-02T12:01:00Z",
            }))
            valid_id = (await result(websocket, "start"))["transactionId"]

    records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (tmp_path / "transactions").glob("*/*.json")
    ]
    assert len(records) == 1
    assert records[0]["transaction_id"] == valid_id
    assert valid_id != 900000
    unresolved = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (tmp_path / "transactions-unresolved").glob("*/*.json")
    ]
    assert any(
        item["transaction_id"] == 900000
        and item["message_type"] == "StopTransaction"
        for item in unresolved
    )


@pytest.mark.asyncio
async def test_websocket_start_retry_after_server_restart_commits_before_ack(tmp_path):
    """Wire-level exact retry has one SQLite identity and two incoming receipts."""
    import sqlite3

    payload = {
        "connectorId": 1, "idTag": "card-a", "meterStart": 100,
        "timestamp": "2026-10-02T12:00:00Z",
    }

    async def start_once(message_id):
        server = CSMSServer(
            host="127.0.0.1", port=0,
            transactions=TransactionArchive(tmp_path),
            events=EventStore(tmp_path),
        )
        async with websockets.serve(
            server.accept, "127.0.0.1", 0,
            subprotocols=[OCPP_16_SUBPROTOCOL],
        ) as listener:
            port = listener.sockets[0].getsockname()[1]
            async with websockets.connect(
                f"ws://127.0.0.1:{port}/ocpp/charger-a",
                subprotocols=[OCPP_16_SUBPROTOCOL],
            ) as websocket:
                await websocket.send(call(message_id, "StartTransaction", payload))
                response = await result(websocket, message_id)
                transaction_id = response["transactionId"]
                # The database commit must be visible before the response is observed.
                with sqlite3.connect(tmp_path / "ocpp-csms.sqlite3") as connection:
                    assert connection.execute(
                        "SELECT transaction_id FROM transactions WHERE transaction_id = ?",
                        (transaction_id,),
                    ).fetchone() == (transaction_id,)
                    assert connection.execute(
                        """SELECT COUNT(*) FROM events
                           WHERE action = 'StartTransaction' AND direction = 'in'
                             AND transaction_id = ?""",
                        (transaction_id,),
                    ).fetchone()[0] == (1 if message_id == "first" else 2)
                return transaction_id

    first = await start_once("first")
    retry = await start_once("retry")
    assert retry == first == 1
