"""Choose the charger-facing HTTPS port, with no public bind or side effects.

Prefer the already managed listener for idempotence. For a new deployment
probe a small deterministic unprivileged port list. Explicit overrides must
be available before any persistent change or service reload.
"""
from __future__ import annotations

import argparse
import ipaddress
import re
import socket
from pathlib import Path

DEFAULT_PORTS = (8443, 9443, 10443, 11443, 12443, 13443)
MARKER = "Managed by ocpp-csms private diagnostics"


def existing_listener(site: Path) -> tuple[str, int] | None:
    if not site.is_file():
        return None
    body = site.read_text(encoding="utf-8")
    if MARKER not in body:
        return None
    match = re.search(r"(?m)^\s*listen\s+(\d+\.\d+\.\d+\.\d+):(\d+)\s+ssl;", body)
    return (match.group(1), int(match.group(2))) if match else None


def existing_port(site: Path, bind_ip: str) -> int | None:
    current = existing_listener(site)
    return current[1] if current is not None and current[0] == bind_ip else None


def port_is_available(bind_ip: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((bind_ip, port))
        except OSError:
            return False
        return True


def choose(bind_ip: str, site: Path, requested: int | None = None) -> int:
    ip = ipaddress.ip_address(bind_ip)
    if not isinstance(ip, ipaddress.IPv4Address):
        raise ValueError("IPv4 required")
    old = existing_listener(site)
    if requested is not None and not (443 <= requested <= 65535):
        raise ValueError("explicit port outside permitted range 443-65535")
    if requested is not None:
        if old == (bind_ip, requested) or port_is_available(bind_ip, requested):
            return requested
        raise ValueError("requested port already occupied")
    if old is not None:
        old_ip, old_port = old
        if old_ip == bind_ip or port_is_available(bind_ip, old_port):
            return old_port
    for port in DEFAULT_PORTS:
        if port_is_available(bind_ip, port):
            return port
    raise ValueError("no private HTTPS port available: supply --diagnostics-port")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ip", required=True)
    parser.add_argument("--site", required=True, type=Path)
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    try:
        print(choose(args.ip, args.site, args.port))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
