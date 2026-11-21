"""Fail-closed, read-only live Basic-auth preflight for public OCPP WSS exposure.

Connect without credentials to the same loopback WS upstream that nginx uses.
Only the running CSMS's specific HTTP 401 Basic challenge authorizes expose.
The probe never authenticates, claims a charge point or sends OCPP traffic.
"""
from __future__ import annotations

import argparse
import base64
import re
import secrets
import socket

MAX_HEADER = 8192


def probe(port: int, timeout: float = 4.0) -> None:
    """Reject absent, stale, anonymous or otherwise unverified CSMS listeners."""
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("invalid upstream port")
    if not 0 < timeout <= 30:
        raise ValueError("invalid timeout")
    # A unique, impossible-to-collide test identity keeps an anonymous
    # (incorrectly configured) CSMS from displacing a physical charger.
    path = "/__ocpp_auth_preflight_" + secrets.token_hex(16)
    key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "Sec-WebSocket-Protocol: ocpp1.6\r\n"
        "\r\n"
    ).encode("ascii")
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as conn:
        conn.settimeout(timeout)
        conn.sendall(request)
        response = bytearray()
        while b"\r\n\r\n" not in response and len(response) <= MAX_HEADER:
            chunk = conn.recv(min(2048, MAX_HEADER + 1 - len(response)))
            if not chunk:
                break
            response.extend(chunk)
    head = bytes(response).split(b"\r\n\r\n", 1)[0]
    if len(head) > MAX_HEADER:
        raise ValueError("oversized handshake response")
    lines = head.decode("latin-1").split("\r\n")
    status = re.fullmatch(r"HTTP/1\.[01] 401(?: .*)?", lines[0]) is not None
    challenges = [line.split(":", 1)[1].strip() for line in lines[1:]
                  if line.lower().startswith("www-authenticate:")]
    challenged = any(re.fullmatch(r'basic\s+realm="ocpp-csms"', value, re.I)
                     for value in challenges)
    if not status or not challenged:
        raise ValueError("live CSMS did not reject anonymous WebSocket with expected Basic challenge")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True,
                        help="nginx's local CSMS WebSocket upstream TCP port")
    args = parser.parse_args()
    try:
        probe(args.port)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Refusing public exposure: {exc}\n")
    print("Verified: running CSMS rejects anonymous WebSocket upgrade with Basic 401")


if __name__ == "__main__":
    main()
