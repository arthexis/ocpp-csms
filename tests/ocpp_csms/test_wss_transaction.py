"""Real OCPP 1.6J transaction over authenticated WSS with persisted evidence."""
import json
import socket
import ssl
import subprocess

import pytest
import websockets

from ocpp_csms.auth import basic_upgrade_check, create_basic, load_auth
from ocpp_csms.evidence.store import EventStore
from ocpp_csms.server import CSMSServer, OCPP_16_SUBPROTOCOL
from ocpp_csms.tls_config import TLSConfig, write_config
from ocpp_csms.tls_listener import TLSListener


def call(id, action, data):
    return json.dumps([2, id, action, data])


async def response(socket, id):
    kind, returned_id, payload = json.loads(await socket.recv())
    assert kind == 3 and returned_id == id
    return payload


@pytest.mark.asyncio
async def test_claim_and_full_transaction_over_wss(tmp_path):
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(key), "-out", str(cert), "-days", "2",
         "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost"],
        capture_output=True, check=True,
    )
    auth_file = tmp_path / "auth.json"
    create_basic(None, "test-password", auth_file, limit="3d")
    config = load_auth(auth_file)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    tls_file = tmp_path / "tls.json"
    write_config(TLSConfig(str(cert), str(key), "localhost", port, True), tls_file)

    from ocpp_csms.transactions.archive import TransactionArchive
    server = CSMSServer("127.0.0.1", 9000, TransactionArchive(tmp_path), EventStore(tmp_path))
    listener = TLSListener(tls_file)
    assert await listener.start(
        server.accept, host="127.0.0.1", ws_port=9000,
        subprotocol=OCPP_16_SUBPROTOCOL,
        process_request=lambda path, headers: basic_upgrade_check(
            config, path, headers, secure=True, config_path=auth_file,
        ),
    )
    client_tls = ssl.create_default_context(cafile=str(cert))
    import base64
    def credentials(name, password="test-password"):
        encoded = base64.b64encode(f"{name}:{password}".encode()).decode()
        return {"Authorization": "Basic " + encoded}

    try:
        url = f"wss://localhost:{port}/Vendor-SERIAL-001"
        async with websockets.connect(
            url, ssl=client_tls, subprotocols=["ocpp1.6"],
            extra_headers=credentials("Vendor-SERIAL-001"),
        ) as ws:
            await ws.send(call("boot", "BootNotification",
                               {"chargePointVendor": "Vendor", "chargePointModel": "Simulator"}))
            assert (await response(ws, "boot"))["status"] == "Accepted"
            await ws.send(call("heartbeat", "Heartbeat", {}))
            assert "currentTime" in await response(ws, "heartbeat")
            await ws.send(call("auth", "Authorize", {"idTag": "TEST"}))
            assert (await response(ws, "auth"))["idTagInfo"]["status"] == "Accepted"
            await ws.send(call("start", "StartTransaction",
                               {"connectorId": 1, "idTag": "TEST",
                                "meterStart": 100, "timestamp": "2026-10-12T12:00:00Z"}))
            transaction = await response(ws, "start")
            txn_id = transaction["transactionId"]
            await ws.send(call("meter", "MeterValues",
                               {"connectorId": 1, "transactionId": txn_id,
                                "meterValue": [{"timestamp": "2026-10-12T12:01:00Z",
                                                "sampledValue": [{"value": "120"}]}]}))
            assert await response(ws, "meter") == {}
            await ws.send(call("stop", "StopTransaction",
                               {"transactionId": txn_id, "meterStop": 130,
                                "timestamp": "2026-10-12T12:02:00Z"}))
            assert await response(ws, "stop") == {}
        assert load_auth(auth_file)["username"] == "Vendor-SERIAL-001"
        with pytest.raises(websockets.exceptions.InvalidStatusCode) as err:
            async with websockets.connect(
                f"wss://localhost:{port}/OTHER-CHARGER", ssl=client_tls,
                extra_headers=credentials("OTHER-CHARGER"),
                subprotocols=["ocpp1.6"],
            ):
                pass
        assert err.value.status_code == 401
        async with websockets.connect(
            url, ssl=client_tls, extra_headers=credentials("Vendor-SERIAL-001"),
            subprotocols=["ocpp1.6"],
        ) as ws:
            await ws.send(call("boot2", "BootNotification",
                               {"chargePointVendor": "Vendor", "chargePointModel": "Simulator"}))
            assert (await response(ws, "boot2"))["status"] == "Accepted"
    finally:
        await listener.stop()

    with EventStore(tmp_path)._connect() as connection:
        saved = connection.execute(
            "SELECT charger_id, transaction_id, state, meter_stop FROM transactions WHERE transaction_id = ?",
            (txn_id,),
        ).fetchone()
        actions = connection.execute(
            "SELECT action FROM events WHERE transaction_id = ? AND direction = 'in' ORDER BY id",
            (txn_id,),
        ).fetchall()
    assert saved == ("Vendor-SERIAL-001", txn_id, "stopped", 130)
    assert [action for (action,) in actions] == ["StartTransaction", "MeterValues", "StopTransaction"]
