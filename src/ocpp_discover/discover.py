from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import shutil
import subprocess
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from ocpp_discover import redirect as redirect_tools
from ocpp_discover.redirect import RedirectReceipt, WebSocketRequest
from ocpp_csms.status import appliance_status

_DEFAULT_INTERFACE = "eth0"
_DEFAULT_SECONDS = 15.0
_MIN_REQUESTS = 2
_ADDRESS_STATE = "address.json"
_DISCOVERY_STATE = "discovery.json"
_REDIRECT_STATE = "redirect.json"

_ARP_REQUEST = re.compile(
    r"^(?P<time>\d\d:\d\d:\d\d(?:\.\d+)?)\s+"
    r"(?P<src_mac>[0-9a-f:]{17})\s+>\s+(?P<dst_mac>[0-9a-f:]{17}),.*?"
    r"ARP.*?Request who-has (?P<target_ip>\d+\.\d+\.\d+\.\d+) "
    r"tell (?P<source_ip>\d+\.\d+\.\d+\.\d+)",
    re.IGNORECASE,
)
_ARP_REPLY = re.compile(
    r"ARP.*?Reply (?P<ip>\d+\.\d+\.\d+\.\d+) is-at (?P<mac>[0-9a-f:]{17})",
    re.IGNORECASE,
)
_TCP_PACKET = re.compile(
    r"^(?P<time>\d\d:\d\d:\d\d(?:\.\d+)?)\s+IP\s+"
    r"(?P<src>\d+\.\d+\.\d+\.\d+)\.(?P<src_port>\d+)\s+>\s+"
    r"(?P<dst>\d+\.\d+\.\d+\.\d+)\.(?P<dst_port>\d+):",
    re.MULTILINE,
)
_GET = re.compile(r"GET\s+(?P<path>\S+)\s+HTTP/1\.[01]", re.IGNORECASE)
_HOST = re.compile(r"(?im)^Host:\s*(?P<host>\S+)\s*$")
_UPGRADE = re.compile(r"(?im)^Upgrade:\s*websocket\s*$")
_CONNECTION = re.compile(r"(?im)^Connection:\s*(?P<value>[^\r\n]+)$")
_INTERFACE = re.compile(r"^[A-Za-z0-9_.:-]+$")
_MAC = re.compile(r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$", re.IGNORECASE)


@dataclass(frozen=True)
class DiscoveryCandidate:
    interface: str
    source_mac: str
    source_ip: str
    target_ip: str
    requests: int

    def to_json(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class AddressClaim:
    interface: str
    address: str

    def to_json(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class DiscoveryResult:
    status: str
    charger_id: str | None
    candidate: DiscoveryCandidate | None = None
    redirect: RedirectReceipt | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "status": self.status,
            "charger_id": self.charger_id,
            "candidate": self.candidate.to_json() if self.candidate else None,
            "redirect": self.redirect.to_json() if self.redirect else None,
        }


def require_root() -> None:
    if os.geteuid() != 0:
        raise RuntimeError("root_required")


def _validate_interface(interface: str) -> str:
    if not interface or not _INTERFACE.fullmatch(interface):
        raise ValueError("invalid_interface")
    return interface


def _validate_ipv4(value: str, error: str = "invalid_ipv4") -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise ValueError(error) from None
    if address.version != 4:
        raise ValueError(error)
    return str(address)


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=check)


def capture_arp(interface: str, seconds: float) -> str:
    _validate_interface(interface)
    if seconds <= 0:
        raise ValueError("seconds_must_be_positive")
    if shutil.which("tcpdump") is None:
        raise RuntimeError("tcpdump_not_found")

    process = subprocess.Popen(
        ["tcpdump", "-i", interface, "-l", "-nn", "-e", "arp"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=seconds)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
    if process.returncode not in {0, -15}:
        detail = stderr.strip().splitlines()[-1] if stderr.strip() else "capture_failed"
        raise RuntimeError(detail)
    return stdout


def discover_candidate(text: str, *, interface: str, min_requests: int = _MIN_REQUESTS) -> DiscoveryCandidate:
    _validate_interface(interface)
    requests: Counter[tuple[str, str, str]] = Counter()
    replies: set[str] = set()
    for line in text.splitlines():
        reply = _ARP_REPLY.search(line)
        if reply:
            replies.add(reply.group("ip"))
        request = _ARP_REQUEST.search(line)
        if request:
            requests[(request.group("src_mac").lower(), request.group("source_ip"), request.group("target_ip"))] += 1

    candidates = [
        DiscoveryCandidate(interface, source_mac, source_ip, target_ip, count)
        for (source_mac, source_ip, target_ip), count in requests.items()
        if count >= min_requests and target_ip not in replies
    ]
    if not candidates:
        raise ValueError("no_unanswered_arp_candidate")
    candidates.sort(key=lambda item: item.requests, reverse=True)
    if len(candidates) > 1 and candidates[0].requests == candidates[1].requests:
        raise ValueError("ambiguous_arp_candidates")
    return candidates[0]


def discover(*, interface: str = _DEFAULT_INTERFACE, seconds: float = _DEFAULT_SECONDS, min_requests: int = _MIN_REQUESTS) -> DiscoveryCandidate:
    return discover_candidate(capture_arp(interface, seconds), interface=interface, min_requests=min_requests)


def interface_addresses(interface: str) -> set[str]:
    _validate_interface(interface)
    result = _run(["ip", "-4", "-o", "addr", "show", "dev", interface])
    addresses: set[str] = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if "inet" in parts:
            addresses.add(parts[parts.index("inet") + 1].split("/", 1)[0])
    return addresses


def claim_address(candidate: DiscoveryCandidate, *, run_dir: str | Path) -> AddressClaim | None:
    require_root()
    if candidate.target_ip in interface_addresses(candidate.interface):
        return None
    _run(["ip", "addr", "add", f"{candidate.target_ip}/32", "dev", candidate.interface])
    claim = AddressClaim(candidate.interface, candidate.target_ip)
    path = Path(run_dir) / _ADDRESS_STATE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(claim.to_json(), sort_keys=True) + "\n", encoding="utf-8")
    return claim


def remove_claim(run_dir: str | Path) -> None:
    require_root()
    path = Path(run_dir) / _ADDRESS_STATE
    if not path.exists():
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    interface = _validate_interface(str(payload["interface"]))
    address = _validate_ipv4(str(payload["address"]))
    _run(["ip", "addr", "del", f"{address}/32", "dev", interface], check=False)
    path.unlink(missing_ok=True)


def wait_for_charger(data_dir: str | Path, *, timeout: float) -> str | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = appliance_status(data_dir)
        connected = [item for item in status.get("chargers", []) if item.get("connected")]
        if connected:
            return str(connected[0]["charger_id"])
        time.sleep(0.25)
    return None


def _write_discovery_state(run_dir: str | Path, candidate: DiscoveryCandidate) -> None:
    path = Path(run_dir) / _DISCOVERY_STATE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(candidate.to_json(), sort_keys=True) + "\n", encoding="utf-8")


def _load_discovery_state(run_dir: str | Path) -> DiscoveryCandidate | None:
    path = Path(run_dir) / _DISCOVERY_STATE
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return DiscoveryCandidate(
        interface=_validate_interface(str(payload["interface"])),
        source_mac=str(payload["source_mac"]),
        source_ip=_validate_ipv4(str(payload["source_ip"])),
        target_ip=_validate_ipv4(str(payload["target_ip"])),
        requests=int(payload["requests"]),
    )


def _capture_tcp(interface: str, seconds: float, passive_capture_log: str | None) -> str:
    text = redirect_tools.capture_text(interface, seconds)
    if passive_capture_log:
        path = Path(passive_capture_log).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return text


def discover_existing_endpoint(
    *,
    interface: str,
    listen_port: int,
    seconds: float,
    passive_capture_log: str | None = None,
) -> RedirectReceipt:
    return redirect_tools.parse_capture(
        _capture_tcp(interface, seconds, passive_capture_log),
        interface=interface,
        listen_port=listen_port,
    )


def _run_once(
    *,
    data_dir: str,
    state_dir: str,
    interface: str,
    listen_port: int,
    grace_seconds: float,
    arp_seconds: float,
    tcp_seconds: float,
    existing_endpoint_only: bool,
    passive_diagnostic_only: bool,
    passive_capture_log: str | None,
) -> DiscoveryResult:
    require_root()
    existing = wait_for_charger(data_dir, timeout=grace_seconds)
    if existing:
        return DiscoveryResult("connected", existing)

    try:
        receipt = discover_existing_endpoint(
            interface=interface,
            listen_port=listen_port,
            seconds=tcp_seconds,
            passive_capture_log=passive_capture_log,
        )
    except ValueError:
        receipt = None

    if receipt is not None:
        if passive_diagnostic_only:
            return DiscoveryResult("observed", None, redirect=receipt)
        if receipt.destination_ips and all(ip in interface_addresses(interface) for ip in receipt.destination_ips):
            redirect_tools.apply_redirect(receipt, state_dir)
            charger_id = wait_for_charger(data_dir, timeout=grace_seconds)
            return DiscoveryResult("redirected", charger_id, redirect=receipt)
        if existing_endpoint_only:
            raise ValueError("observed_endpoint_not_local")

    if existing_endpoint_only:
        raise ValueError("no_existing_endpoint")

    candidate = discover(interface=interface, seconds=arp_seconds)
    _write_discovery_state(state_dir, candidate)
    claim_address(candidate, run_dir=state_dir)

    receipt = discover_existing_endpoint(
        interface=interface,
        listen_port=listen_port,
        seconds=tcp_seconds,
        passive_capture_log=passive_capture_log,
    )
    redirect_tools.apply_redirect(receipt, state_dir)
    charger_id = wait_for_charger(data_dir, timeout=grace_seconds)
    return DiscoveryResult("redirected", charger_id, candidate=candidate, redirect=receipt)


def cleanup(*, state_dir: str) -> None:
    require_root()
    if redirect_tools.table_exists():
        redirect_tools.remove_redirect(state_dir)
    remove_claim(state_dir)
    Path(state_dir, _DISCOVERY_STATE).unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Discover a charger's configured OCPP endpoint and adapt the local host safely.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run")
    run.add_argument("--data-dir", required=True)
    run.add_argument("--state-dir", required=True)
    run.add_argument("--interface", default=_DEFAULT_INTERFACE)
    run.add_argument("--listen-port", type=int, default=9000)
    run.add_argument("--grace-seconds", type=float, default=10.0)
    run.add_argument("--arp-seconds", type=float, default=_DEFAULT_SECONDS)
    run.add_argument("--tcp-seconds", type=float, default=_DEFAULT_SECONDS)
    run.add_argument("--existing-endpoint-only", action="store_true")
    run.add_argument("--passive-diagnostic-only", action="store_true")
    run.add_argument("--passive-capture-log")

    cleanup_parser = subparsers.add_parser("cleanup")
    cleanup_parser.add_argument("--state-dir", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "cleanup":
            cleanup(state_dir=args.state_dir)
            return 0
        result = _run_once(
            data_dir=args.data_dir,
            state_dir=args.state_dir,
            interface=args.interface,
            listen_port=args.listen_port,
            grace_seconds=args.grace_seconds,
            arp_seconds=args.arp_seconds,
            tcp_seconds=args.tcp_seconds,
            existing_endpoint_only=args.existing_endpoint_only,
            passive_diagnostic_only=args.passive_diagnostic_only,
            passive_capture_log=args.passive_capture_log,
        )
    except (RuntimeError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(str(exc))
        return 1
    print(json.dumps(result.to_json(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
