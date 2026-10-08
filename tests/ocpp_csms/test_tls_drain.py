"""Real-socket tests for listener drain and make-before-break port changes."""
from __future__ import annotations

import asyncio
import socket
import ssl
import subprocess

import pytest
import websockets

from ocpp_csms import tls_config
from ocpp_csms.tls_listener import TLSListener


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def certificate(tmp_path):
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(key), "-out", str(cert), "-days", "2",
        "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
    ], check=True, capture_output=True)
    return cert, key


@pytest.mark.asyncio
async def test_graceful_disable_and_port_migration(tmp_path):
    cert, key = certificate(tmp_path)
    config_path = tmp_path / "tls.json"
    old_port, new_port = free_port(), free_port()
    while old_port == new_port:
        new_port = free_port()
    config = tls_config.TLSConfig(str(cert), str(key), "localhost", old_port, True)
    tls_config.write_config(config, config_path)
    async def handler(ws):
        async for value in ws:
            await ws.send(value)
    listener = TLSListener(config_path)
    assert await listener.start(handler, host="127.0.0.1", ws_port=9000, subprotocol="ocpp1.6")
    trusted = ssl.create_default_context(cafile=str(cert))
    try:
        async with websockets.connect(f"wss://localhost:{old_port}/old", ssl=trusted) as existing:
            tls_config.write_config(tls_config.TLSConfig(str(cert), str(key), "localhost", new_port, True), config_path)
            migrated = await listener.command("reload")
            assert migrated["ok"]
            assert listener.status()["active_port"] == new_port
            assert old_port in listener.status()["draining_ports"]
            await existing.send("old still works")
            assert await existing.recv() == "old still works"
            async with websockets.connect(f"wss://localhost:{new_port}/new", ssl=trusted) as newer:
                await newer.send("new works")
                assert await newer.recv() == "new works"
                disabled = await listener.command("disable")
                assert disabled["ok"]
                assert listener.status()["listener"] == "draining"
                await existing.send("old alive")
                await newer.send("new alive")
                assert await existing.recv() == "old alive"
                assert await newer.recv() == "new alive"
            await asyncio.sleep(0)
            assert listener.server is None
        await asyncio.wait_for(asyncio.gather(*(item.cleanup for item in list(listener._draining))), 5)
        assert listener.status()["listener"] == "stopped"
    finally:
        await listener.stop()


@pytest.mark.asyncio
async def test_failed_new_port_bind_retains_old_port(tmp_path):
    cert, key = certificate(tmp_path)
    config_path = tmp_path / "tls.json"
    old_port = free_port()
    tls_config.write_config(tls_config.TLSConfig(str(cert), str(key), "localhost", old_port, True), config_path)
    async def handler(ws):
        async for value in ws:
            await ws.send(value)
    listener = TLSListener(config_path)
    assert await listener.start(handler, host="127.0.0.1", ws_port=9000, subprotocol="ocpp1.6")
    occupied = socket.socket()
    occupied.bind(("127.0.0.1", 0))
    occupied.listen()
    try:
        port = occupied.getsockname()[1]
        tls_config.write_config(tls_config.TLSConfig(str(cert), str(key), "localhost", port, True), config_path)
        response = await listener.command("reload")
        assert response["error"] == "tls_operation_failed"
        assert listener.status()["active_port"] == old_port
        trusted = ssl.create_default_context(cafile=str(cert))
        async with websockets.connect(f"wss://localhost:{old_port}/old", ssl=trusted) as ws:
            await ws.send("alive")
            assert await ws.recv() == "alive"
    finally:
        occupied.close()
        await listener.stop()
