"""Regression tests for Basic auth before the WebSocket upgrade."""
from __future__ import annotations

import asyncio
import base64
import json
import ssl
import subprocess
from http import HTTPStatus

import pytest
import websockets

from ocpp_csms.auth import basic_upgrade_check, create_basic, load_auth
from ocpp_csms.cli import build_parser
from ocpp_csms.tls_config import TLSConfig, write_config
from ocpp_csms.tls_listener import TLSListener


def header(username, password):
    encoded = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return {"Authorization": "Basic " + encoded}


def test_hashed_credentials_and_path_identity(tmp_path):
    config_path = tmp_path / "auth.json"
    create_basic("ABB-001", "a-long-secret", config_path)
    config = load_auth(config_path)
    assert config_path.stat().st_mode & 0o777 == 0o600
    assert config["mode"] == "basic"
    assert "a-long-secret" not in config_path.read_text()
    assert basic_upgrade_check(config, "/ABB-001", header("ABB-001", "a-long-secret"), secure=True) is None
    assert basic_upgrade_check(config, "/alias/ABB-001?query=1", header("ABB-001", "a-long-secret"), secure=True) is None
    for path, creds in (
        ("/ABB-002", header("ABB-001", "a-long-secret")),
        ("/ABB-001", header("ABB-002", "a-long-secret")),
        ("/ABB-001", header("ABB-001", "wrong")),
        ("/ABB-001", {}),
        ("/ABB-001", {"Authorization": "Basic garbage==="}),
    ):
        reply = basic_upgrade_check(config, path, creds, secure=True)
        assert reply[0] == HTTPStatus.UNAUTHORIZED
    assert basic_upgrade_check(config, "/ABB-001", header("ABB-001", "a-long-secret"), secure=False)[0] == HTTPStatus.FORBIDDEN


def test_bad_auth_config_fails_closed(tmp_path):
    config = tmp_path / "auth.json"
    config.write_text('{"mode": "basic", "username": "ABB-001"}')
    with pytest.raises(ValueError, match="invalid_auth_configuration"):
        load_auth(config)
    config.write_text('{"mode": "typo"}')
    with pytest.raises(ValueError, match="invalid_auth_configuration"):
        load_auth(config)


def test_auth_cli_parser():
    parser, _ = build_parser()
    args = parser.parse_args(["auth", "basic", "--cp", "ABB-001"])
    assert (args.auth_action, args.cp) == ("basic", "ABB-001")


@pytest.mark.asyncio
async def test_real_wss_handshake_rejects_missing_or_wrong_basic(tmp_path):
    cert = tmp_path / "server.crt"
    key = tmp_path / "server.key"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", str(key), "-out", str(cert), "-days", "2",
                    "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost"],
                   capture_output=True, check=True)
    auth_file = tmp_path / "auth.json"
    create_basic("ABB-001", "the-password", auth_file)
    auth = load_auth(auth_file)
    config_path = tmp_path / "tls.json"
    import socket
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    write_config(TLSConfig(str(cert), str(key), "localhost", port, True), config_path)
    accepted = []
    async def handler(ws):
        accepted.append(ws.path)
        await ws.send("ok")

    listener = TLSListener(config_path)
    assert await listener.start(
        handler, host="127.0.0.1", ws_port=9000, subprotocol="ocpp1.6",
        process_request=lambda path, headers: basic_upgrade_check(auth, path, headers, secure=True),
    )
    client_ssl = ssl.create_default_context(cafile=str(cert))
    try:
        url = f"wss://localhost:{port}/ABB-001"
        for supplied in ({}, header("ABB-001", "bad")):
            with pytest.raises(websockets.exceptions.InvalidStatusCode) as exc:
                async with websockets.connect(url, ssl=client_ssl, extra_headers=supplied,
                                              subprotocols=["ocpp1.6"]):
                    pass
            assert exc.value.status_code == 401
        async with websockets.connect(url, ssl=client_ssl, extra_headers=header("ABB-001", "the-password"),
                                      subprotocols=["ocpp1.6"]) as ws:
            assert await ws.recv() == "ok"
        assert accepted == ["/ABB-001"]
    finally:
        await listener.stop()
