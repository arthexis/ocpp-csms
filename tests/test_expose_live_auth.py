"""Regression checks for fail-closed live Basic auth before public exposure."""
from __future__ import annotations

import importlib.util
import socketserver
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/verify_runtime_basic.py"
ROLE = ROOT / "ansible/roles/ocpp_expose/tasks/main.yml"

spec = importlib.util.spec_from_file_location("verify_runtime_basic", HELPER)
assert spec and spec.loader
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


class HandshakeHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(3)
        request = bytearray()
        while b"\r\n\r\n" not in request and len(request) < 8192:
            chunk = self.request.recv(4096)
            if not chunk:
                break
            request.extend(chunk)
        self.server.captured = bytes(request)
        self.request.sendall(self.server.reply)


@pytest.fixture
def listener():
    services = []

    def start(reply):
        server = socketserver.TCPServer(("127.0.0.1", 0), HandshakeHandler)
        server.reply = reply
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        services.append((server, thread))
        return server

    yield start
    for server, thread in services:
        server.shutdown()
        server.server_close()
        thread.join(2)


BASIC_CHALLENGE = (
    b'HTTP/1.1 401 Unauthorized\r\n'
    b'WWW-Authenticate: Basic realm="ocpp-csms"\r\n'
    b'Content-Length: 0\r\n\r\n'
)


def test_live_basic_challenge_succeeds_without_secret_or_ocpp_traffic(listener):
    server = listener(BASIC_CHALLENGE)
    live.probe(server.server_address[1])
    request = server.captured
    assert request.startswith(b"GET /__ocpp_auth_preflight_")
    assert b"Upgrade: websocket\r\n" in request
    assert b"Sec-WebSocket-Protocol: ocpp1.6\r\n" in request
    assert b"Authorization:" not in request
    assert b"BootNotification" not in request


@pytest.mark.parametrize("response", [
    b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n\r\n",
    b"HTTP/1.1 401 Unauthorized\r\n\r\n",
    b'HTTP/1.1 401 Unauthorized\r\nWWW-Authenticate: Basic realm="other"\r\n\r\n',
    b"HTTP/1.1 403 Forbidden\r\n\r\n",
    b"HTTP/1.1 404 Not Found\r\n\r\n",
    b"HTTP/1.1 500 Server Error\r\n\r\n",
    b"not-http\r\n\r\n",
    b'HTTP/1.1 401 Unauthorized\r\nWWW-Authenticate: Basic realm="ocpp-csms"'
    + b"A" * 8200 + b"\r\n\r\n",
])
def test_stale_anonymous_or_unverifiable_listener_fails_closed(listener, response):
    server = listener(response)
    with pytest.raises(ValueError):
        live.probe(server.server_address[1])


@pytest.mark.parametrize("port", [-1, 0, 65536, "9000", True])
def test_invalid_port_is_rejected(port):
    with pytest.raises(ValueError, match="port"):
        live.probe(port)


def test_live_check_precedes_any_exposure_mutation():
    text = ROLE.read_text()
    assert text.index("Verify Basic credentials are configured") < text.index(
        "Reject anonymous public CSMS"
    )
    assert text.index("Reject anonymous public CSMS") < text.index(
        "Verify the running CSMS actively enforces Basic before public exposure"
    )
    assert text.index("Verify the running CSMS actively enforces Basic") < text.index(
        "Install nginx and Certbot"
    )
    assert "scripts/verify_runtime_basic.py" in text
    assert "ocpp_expose_upstream_port | string" in text
