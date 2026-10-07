from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass

from ocpp_discover import discover

_OCPP_SUBPROTOCOL = re.compile(
    r"(?im)^Sec-WebSocket-Protocol:\s*[^\r\n]*\bocpp(?:1\.6|2\.0\.1)\b"
)
_OCPP_FRAME = re.compile(
    r'\[(?:2|3|4)\s*,\s*"[^"\r\n]{1,128}"(?:\s*,|\s*\])'
)
_PID = re.compile(r"pid=(\d+)")
_ESTABLISHED_SOCKET = re.compile(
    r"^\S+\s+\S+\s+(?P<local_ip>\d+\.\d+\.\d+\.\d+):(?P<local_port>\d+)\s+"
    r"(?P<peer_ip>\d+\.\d+\.\d+\.\d+):(?P<peer_port>\d+)\s+(?P<process>.*)$"
)
_SERVICE_CGROUP = re.compile(r"/([^/]+\.service)(?:/|$)")


@dataclass(frozen=True)
class IncumbentEndpoint:
    service: str
    port: int
    pid: int
    peer_ip: str
    evidence: str

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def _observed_endpoints(text: str, local_addresses: set[str]) -> set[tuple[int, str, str]]:
    endpoints: set[tuple[int, str, str]] = set()
    for packet, payload in discover._tcp_blocks(text):
        source_ip = packet.group("src")
        destination_ip = packet.group("dst")
        source_port = int(packet.group("src_port"))
        destination_port = int(packet.group("dst_port"))

        if destination_ip in local_addresses and _OCPP_SUBPROTOCOL.search(payload):
            endpoints.add((destination_port, source_ip, "websocket_subprotocol"))
            continue

        if source_ip in local_addresses and _OCPP_FRAME.search(payload):
            endpoints.add((source_port, destination_ip, "ocpp_frame"))

    return endpoints


def observe_endpoint(interface: str, seconds: float) -> tuple[int, str, str] | None:
    local_addresses = discover.host_addresses()
    capture = discover.capture_passive_tcp(interface, seconds)
    endpoints = _observed_endpoints(capture, local_addresses)
    if not endpoints:
        return None

    ports_and_peers = {(port, peer) for port, peer, _ in endpoints}
    if len(ports_and_peers) != 1:
        raise RuntimeError("ambiguous_local_ocpp_endpoints")

    port, peer = next(iter(ports_and_peers))
    evidence = ",".join(sorted({kind for candidate_port, candidate_peer, kind in endpoints if candidate_port == port and candidate_peer == peer}))
    return port, peer, evidence


def _socket_pids(command: list[str], error: str) -> list[int]:
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or error
        raise RuntimeError(detail)
    return sorted({int(value) for value in _PID.findall(result.stdout)})


def listener_pids(port: int) -> list[int]:
    return _socket_pids(
        ["ss", "-H", "-ltnp", f"sport = :{port}"],
        "listener_query_failed",
    )


def established_socket_owners(peer_ip: str) -> list[tuple[int, int]]:
    result = subprocess.run(
        ["ss", "-H", "-tnp", "state", "established"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "established_socket_query_failed"
        raise RuntimeError(detail)

    owners: set[tuple[int, int]] = set()
    for line in result.stdout.splitlines():
        match = _ESTABLISHED_SOCKET.match(line.strip())
        if not match or match.group("peer_ip") != peer_ip:
            continue
        local_port = int(match.group("local_port"))
        owners.update((local_port, int(value)) for value in _PID.findall(match.group("process")))
    return sorted(owners)


def service_for_pid(pid: int) -> str | None:
    try:
        cgroup = open(f"/proc/{pid}/cgroup", encoding="utf-8").read()
    except OSError:
        return None
    services = {match.group(1) for match in _SERVICE_CGROUP.finditer(cgroup)}
    if len(services) == 1:
        return next(iter(services))
    if len(services) > 1:
        raise RuntimeError("ambiguous_listener_systemd_service")
    return None


def resolve_incumbent(interface: str, seconds: float, managed_service: str) -> IncumbentEndpoint | None:
    if os.geteuid() != 0:
        raise RuntimeError("root_required")

    observed = observe_endpoint(interface, seconds)
    if observed is None:
        return None

    observed_port, peer_ip, evidence = observed
    pids = listener_pids(observed_port)
    local_port = observed_port

    if not pids:
        peer_owners = established_socket_owners(peer_ip)
        if not peer_owners:
            raise RuntimeError("observed_ocpp_endpoint_has_no_local_socket_owner")
        local_ports = {port for port, _ in peer_owners}
        if len(local_ports) != 1:
            raise RuntimeError("ambiguous_incumbent_local_ports")
        local_port = next(iter(local_ports))
        pids = sorted({pid for _, pid in peer_owners})

    owners = {(pid, service_for_pid(pid)) for pid in pids}
    resolved = {(pid, service) for pid, service in owners if service}
    services = {service for _, service in resolved}
    if not services:
        raise RuntimeError("observed_ocpp_listener_has_no_systemd_service")
    if len(services) != 1:
        raise RuntimeError("ambiguous_listener_systemd_service")

    service = next(iter(services))
    pid = next(pid for pid, candidate_service in resolved if candidate_service == service)

    if service == managed_service:
        return None

    active = subprocess.run(
        ["systemctl", "is-active", "--quiet", service],
        check=False,
    )
    if active.returncode != 0:
        raise RuntimeError("observed_ocpp_listener_service_not_active")

    return IncumbentEndpoint(
        service=service,
        port=local_port,
        pid=pid,
        peer_ip=peer_ip,
        evidence=evidence,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ocpp_discover.incumbent",
        description="Identify a host-local systemd service proven to be serving OCPP traffic.",
    )
    parser.add_argument("--interface", default="eth0")
    parser.add_argument("--seconds", type=float, default=15.0)
    parser.add_argument("--managed-service", default="ocpp-csms.service")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        endpoint = resolve_incumbent(args.interface, args.seconds, args.managed_service)
    except (RuntimeError, ValueError) as exc:
        print(str(exc))
        return 1

    payload = {"found": endpoint is not None}
    if endpoint is not None:
        payload.update(endpoint.to_json())
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
