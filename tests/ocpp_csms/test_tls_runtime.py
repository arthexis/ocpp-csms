"""Live TLS lifecycle regression tests (no real charger required)."""
from __future__ import annotations

import asyncio
import ssl
import subprocess

import pytest
import websockets

from ocpp_csms import tls_config
from ocpp_csms.tls_listener import TLSListener


def generate_certificate(directory, prefix):
    cert, key = directory / f"{prefix}.crt", directory / f"{prefix}.key"
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(key), "-out", str(cert), "-days", "2",
        "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
    ], capture_output=True, check=True)
    return cert, key


@pytest.mark.asyncio
async def test_enable_and_reload_preserve_live_ws_wss(tmp_path):
    cert, key = generate_certificate(tmp_path, "first")
    replacement_cert, replacement_key = generate_certificate(tmp_path, "second")
    config_path = tmp_path / "tls.json"
    seen = []

    async def handler(ws):
        seen.append(ws.path)
        async for message in ws:
            await ws.send(message)

    async with websockets.serve(handler, "127.0.0.1", 0, subprotocols=["ocpp1.6"]) as plain_server:
        ws_port = plain_server.sockets[0].getsockname()[1]
        # Reserve an ephemeral port, then release it before binding.
        import socket
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            wss_port = probe.getsockname()[1]
        tls_config.write_config(tls_config.TLSConfig(str(cert), str(key), "localhost", wss_port), config_path)
        listener = TLSListener(config_path)
        assert not await listener.start(handler, host="127.0.0.1", ws_port=ws_port, subprotocol="ocpp1.6")
        enabled = await listener.command("enable")
        assert enabled.get("ok") and listener.status()["listener"] == "listening"

        first_tls = ssl.create_default_context(cafile=str(cert))
        async with websockets.connect(f"ws://127.0.0.1:{ws_port}/plain", subprotocols=["ocpp1.6"]) as ws:
            async with websockets.connect(f"wss://localhost:{wss_port}/secure", ssl=first_tls, subprotocols=["ocpp1.6"]) as wss:
                await ws.send("before")
                await wss.send("before")
                assert await ws.recv() == "before"
                assert await wss.recv() == "before"
                tls_config.write_config(tls_config.TLSConfig(str(replacement_cert), str(replacement_key), "localhost", wss_port, True), config_path)
                assert (await listener.command("reload")).get("ok")
                await ws.send("after")
                await wss.send("after")
                assert await ws.recv() == "after"
                assert await wss.recv() == "after"
                # The new handshake must use the replacement certificate.
                second_tls = ssl.create_default_context(cafile=str(replacement_cert))
                async with websockets.connect(f"wss://localhost:{wss_port}/new", ssl=second_tls, subprotocols=["ocpp1.6"]) as new:
                    await new.send("new")
                    assert await new.recv() == "new"
                assert (await listener.command("disable")).get("ok")
                assert listener.status()["listener"] == "draining"
                await ws.send("still charging")
                await wss.send("still charging")
                assert await ws.recv() == "still charging"
                assert await wss.recv() == "still charging"
        await listener.stop()


@pytest.mark.asyncio
async def test_failed_reload_retains_previous_configuration(tmp_path):
    cert, key = generate_certificate(tmp_path, "valid")
    config_path = tmp_path / "tls.json"
    import socket
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    async def handler(ws):
        async for message in ws:
            await ws.send(message)

    tls_config.write_config(tls_config.TLSConfig(str(cert), str(key), "localhost", port, True), config_path)
    listener = TLSListener(config_path)
    assert await listener.start(handler, host="127.0.0.1", ws_port=9000, subprotocol="ocpp1.6")
    try:
        tls_config.write_config(tls_config.TLSConfig("/missing.crt", "/missing.key", "localhost", port, True), config_path)
        result = await listener.command("reload")
        assert result["error"] == "tls_operation_failed"
        tls_client = ssl.create_default_context(cafile=str(cert))
        async with websockets.connect(f"wss://localhost:{port}/test", ssl=tls_client, subprotocols=["ocpp1.6"]) as websocket:
            await websocket.send("alive")
            assert await websocket.recv() == "alive"
    finally:
        await listener.stop()


@pytest.mark.asyncio
async def test_tls_status_without_charger_or_listener(tmp_path):
    listener = TLSListener(tmp_path / "tls.json")
    result = await listener.command("status")
    assert result["ok"]
    assert result["response"]["listener"] == "stopped"
    assert (await listener.command("enable"))["error"] == "tls_operation_failed"
