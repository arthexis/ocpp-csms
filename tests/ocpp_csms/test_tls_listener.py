"""Listener isolation and real WS/WSS handshake regression coverage."""
from __future__ import annotations

import asyncio
import ssl
import subprocess

import pytest
import websockets

from ocpp_csms import tls_config
from ocpp_csms.tls_listener import TLSListener


def _certificate(tmp_path):
    cert = tmp_path / "server.crt"
    key = tmp_path / "server.key"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(cert), "-days", "2",
            "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
        ],
        check=True, capture_output=True,
    )
    return cert, key


@pytest.mark.asyncio
async def test_ws_and_wss_accept_same_handler(tmp_path):
    cert, key = _certificate(tmp_path)
    config_path = tmp_path / "tls.json"
    # The listener reads the persistent configuration on startup.
    seen = []

    async def handler(ws):
        seen.append((ws.path, ws.subprotocol))
        await ws.send("hello")
        await ws.wait_closed()

    async with websockets.serve(handler, "127.0.0.1", 0, subprotocols=["ocpp1.6"]) as ws_server:
        ws_port = ws_server.sockets[0].getsockname()[1]
        listener = TLSListener(config_path)
        # Avoid fixed-port collisions by changing the saved WSS port to an available port.
        with __import__("socket").socket() as probe:
            probe.bind(("127.0.0.1", 0))
            wss_port = probe.getsockname()[1]
        tls_config.write_config(tls_config.TLSConfig(str(cert), str(key), "localhost", wss_port, True), config_path)
        assert await listener.start(handler, host="127.0.0.1", ws_port=ws_port, subprotocol="ocpp1.6")
        try:
            client_tls = ssl.create_default_context(cafile=str(cert))
            async with websockets.connect(f"ws://127.0.0.1:{ws_port}/PLAIN", subprotocols=["ocpp1.6"]) as ws:
                assert await ws.recv() == "hello"
            async with websockets.connect(
                f"wss://localhost:{wss_port}/SECURE", ssl=client_tls, subprotocols=["ocpp1.6"],
            ) as ws:
                assert await ws.recv() == "hello"
        finally:
            await listener.stop()
    assert ("/PLAIN", "ocpp1.6") in seen
    assert ("/SECURE", "ocpp1.6") in seen


@pytest.mark.asyncio
async def test_disabled_tls_has_no_listener(tmp_path):
    config_path = tmp_path / "tls.json"
    listener = TLSListener(config_path)
    assert not await listener.start(lambda ws: None, host="127.0.0.1", ws_port=9000, subprotocol="ocpp1.6")
    assert listener.server is None


@pytest.mark.asyncio
async def test_bad_tls_configuration_does_not_raise_or_start_listener(tmp_path):
    config_path = tmp_path / "tls.json"
    tls_config.write_config(
        tls_config.TLSConfig("/missing/cert.pem", "/missing/key.pem", "localhost", 9443, True),
        config_path,
    )
    listener = TLSListener(config_path)
    assert not await listener.start(lambda ws: None, host="127.0.0.1", ws_port=9000, subprotocol="ocpp1.6")
    assert listener.server is None
    assert "tls_not_ready" in listener.error


@pytest.mark.asyncio
async def test_matching_ws_port_rejected(tmp_path):
    cert, key = _certificate(tmp_path)
    config_path = tmp_path / "tls.json"
    tls_config.write_config(tls_config.TLSConfig(str(cert), str(key), "localhost", 9000, True), config_path)
    listener = TLSListener(config_path)
    assert not await listener.start(lambda ws: None, host="127.0.0.1", ws_port=9000, subprotocol="ocpp1.6")
    assert "tls_port_conflicts_with_ws" in listener.error
