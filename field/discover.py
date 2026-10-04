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

from field import redirect as redirect_tools
from field.redirect import RedirectReceipt, WebSocketRequest
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


def _bounded_tcpdump(command: list[str], seconds: float) -> str:
    if seconds <= 0:
        raise ValueError("seconds_must_be_positive")
    if shutil.which("tcpdump") is None:
        raise RuntimeError("tcpdump_not_found")
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
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


def capture_arp(interface: str, seconds: float) -> str:
    if not _INTERFACE.fullmatch(interface):
        raise ValueError("invalid_interface")
    return _bounded_tcpdump(["tcpdump", "-i", interface, "-l", "-nn", "-e", "arp"], seconds)


def discover_candidate(text: str, *, interface: str = _DEFAULT_INTERFACE, min_requests: int = _MIN_REQUESTS) -> DiscoveryCandidate:
    if min_requests < 1:
        raise ValueError("min_requests_must_be_positive")
    if not _INTERFACE.fullmatch(interface):
        raise ValueError("invalid_interface")
    answered: set[str] = set()
    counts: Counter[tuple[str, str, str]] = Counter()
    for line in text.splitlines():
        reply = _ARP_REPLY.search(line)
        if reply:
            answered.add(reply.group("ip"))
            continue
        request = _ARP_REQUEST.search(line)
        if not request:
            continue
        source_ip = request.group("source_ip")
        target_ip = request.group("target_ip")
        if source_ip == target_ip:
            continue
        counts[(request.group("src_mac").lower(), source_ip, target_ip)] += 1
    candidates = [
        DiscoveryCandidate(interface, source_mac, source_ip, target_ip, count)
        for (source_mac, source_ip, target_ip), count in counts.items()
        if count >= min_requests and target_ip not in answered
    ]
    if not candidates:
        raise ValueError("no_unresolved_arp_candidate")
    if len(candidates) != 1:
        raise ValueError("ambiguous_arp_candidates")
    return candidates[0]


def discover(*, interface: str = _DEFAULT_INTERFACE, seconds: float = _DEFAULT_SECONDS, min_requests: int = _MIN_REQUESTS) -> DiscoveryCandidate:
    return discover_candidate(capture_arp(interface, seconds), interface=interface, min_requests=min_requests)


def require_root() -> None:
    if os.geteuid() != 0:
        raise RuntimeError("root_required")


def _require_ip() -> None:
    if shutil.which("ip") is None:
        raise RuntimeError("ip_not_found")


def _run_ip(command: list[str]) -> subprocess.CompletedProcess[str]:
    _require_ip()
    return subprocess.run(command, text=True, capture_output=True, check=False)


def _ip_error(result: subprocess.CompletedProcess[str], fallback: str) -> RuntimeError:
    detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else fallback
    return RuntimeError(detail)


def _ipv4_addresses(payload: object) -> set[str]:
    try:
        return {
            str(info["local"])
            for item in payload
            for info in item.get("addr_info", [])
            if info.get("family") == "inet" and "local" in info
        }
    except (TypeError, KeyError):
        raise RuntimeError("invalid_ip_address_output") from None


def interface_addresses(interface: str) -> set[str]:
    if not _INTERFACE.fullmatch(interface):
        raise ValueError("invalid_interface")
    result = _run_ip(["ip", "-j", "address", "show", "dev", interface])
    if result.returncode != 0:
        raise _ip_error(result, "interface_address_query_failed")
    try:
        return _ipv4_addresses(json.loads(result.stdout))
    except (TypeError, ValueError, KeyError):
        raise RuntimeError("invalid_ip_address_output") from None


def host_addresses() -> set[str]:
    result = _run_ip(["ip", "-j", "address", "show"])
    if result.returncode != 0:
        raise _ip_error(result, "host_address_query_failed")
    try:
        return _ipv4_addresses(json.loads(result.stdout))
    except (TypeError, ValueError, KeyError):
        raise RuntimeError("invalid_ip_address_output") from None


def _claim_path(state_dir: str | Path) -> Path:
    return Path(state_dir).expanduser() / _ADDRESS_STATE


def load_address_claim(state_dir: str | Path) -> AddressClaim:
    path = _claim_path(state_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        interface = str(payload["interface"])
        address = str(ipaddress.ip_address(payload["address"]))
    except FileNotFoundError:
        raise RuntimeError("address_claim_not_found") from None
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        raise RuntimeError("invalid_address_claim") from None
    if not _INTERFACE.fullmatch(interface) or ":" in address:
        raise RuntimeError("invalid_address_claim")
    return AddressClaim(interface=interface, address=address)


def claim_address(candidate: DiscoveryCandidate, state_dir: str | Path) -> AddressClaim:
    require_root()
    if not _INTERFACE.fullmatch(candidate.interface):
        raise ValueError("invalid_interface")
    address = str(ipaddress.ip_address(candidate.target_ip))
    if ":" in address:
        raise ValueError("target_must_be_ipv4")
    path = _claim_path(state_dir)
    if path.exists():
        raise RuntimeError("address_claim_exists")
    if address in interface_addresses(candidate.interface):
        raise RuntimeError("target_address_already_present")
    claim = AddressClaim(candidate.interface, address)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(claim.to_json(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result = _run_ip(["ip", "address", "add", f"{address}/32", "dev", candidate.interface])
    if result.returncode != 0:
        path.unlink(missing_ok=True)
        raise _ip_error(result, "address_add_failed")
    return claim


def cleanup_address(state_dir: str | Path) -> AddressClaim:
    require_root()
    path = _claim_path(state_dir)
    claim = load_address_claim(state_dir)
    if claim.address in interface_addresses(claim.interface):
        result = _run_ip(["ip", "address", "del", f"{claim.address}/32", "dev", claim.interface])
        if result.returncode != 0:
            raise _ip_error(result, "address_remove_failed")
    path.unlink()
    return claim


def _validate_candidate(candidate: DiscoveryCandidate) -> tuple[str, str]:
    if not _INTERFACE.fullmatch(candidate.interface):
        raise ValueError("invalid_interface")
    if not _MAC.fullmatch(candidate.source_mac):
        raise ValueError("invalid_source_mac")
    try:
        source_ip = ipaddress.ip_address(candidate.source_ip)
    except ValueError:
        raise ValueError("invalid_source_ip") from None
    if source_ip.version != 4:
        raise ValueError("invalid_source_ip")
    return candidate.source_mac.lower(), str(source_ip)


def capture_tcp(candidate: DiscoveryCandidate, seconds: float) -> str:
    source_mac, source_ip = _validate_candidate(candidate)
    packet_filter = f"ether src {source_mac} and ip src {source_ip} and tcp"
    return _bounded_tcpdump(["tcpdump", "-i", candidate.interface, "-l", "-nn", "-s0", "-A", packet_filter], seconds)


def capture_passive_tcp(interface: str, seconds: float) -> str:
    if not _INTERFACE.fullmatch(interface):
        raise ValueError("invalid_interface")
    return _bounded_tcpdump(["tcpdump", "-i", interface, "-l", "-nn", "-s0", "-A", "tcp"], seconds)


def _tcp_blocks(text: str) -> list[tuple[re.Match[str], str]]:
    matches = list(_TCP_PACKET.finditer(text))
    return [(match, text[match.end() : matches[index + 1].start() if index + 1 < len(matches) else len(text)]) for index, match in enumerate(matches)]


def _websocket_receipt(
    text: str,
    *,
    interface: str,
    listen_port: int,
    source_ip: str | None = None,
    destination_filter: set[str] | None = None,
) -> RedirectReceipt:
    if not _INTERFACE.fullmatch(interface):
        raise ValueError("invalid_interface")
    if not 1 <= listen_port <= 65535:
        raise ValueError("invalid_listen_port")
    websocket_candidates: list[tuple[str, str, int, WebSocketRequest]] = []
    saw_tls = False
    for packet, payload in _tcp_blocks(text):
        packet_source = packet.group("src")
        destination_ip = packet.group("dst")
        if source_ip is not None and packet_source != source_ip:
            continue
        if destination_filter is not None and destination_ip not in destination_filter:
            continue
        destination_port = int(packet.group("dst_port"))
        if destination_port == 443:
            saw_tls = True
            continue
        get = _GET.search(payload)
        host = _HOST.search(payload)
        connection = _CONNECTION.search(payload)
        if not (get and host and _UPGRADE.search(payload) and connection):
            continue
        if "upgrade" not in {token.strip().lower() for token in connection.group("value").split(",")}:
            continue
        websocket_candidates.append((packet_source, destination_ip, destination_port, WebSocketRequest(destination_ip, host.group("host"), get.group("path"))))
    if not websocket_candidates:
        if saw_tls:
            raise ValueError("secure_or_opaque_traffic")
        raise ValueError("no_plaintext_websocket_upgrade")
    sources = {item[0] for item in websocket_candidates}
    if len(sources) != 1:
        raise ValueError("ambiguous_websocket_sources")
    ports = {item[2] for item in websocket_candidates}
    if len(ports) != 1:
        raise ValueError("ambiguous_tcp_destinations")
    requests: list[WebSocketRequest] = []
    seen_requests: set[tuple[str, str, str]] = set()
    for _, _, _, request in websocket_candidates:
        key = (request.destination_ip, request.host, request.path)
        if key not in seen_requests:
            seen_requests.add(key)
            requests.append(request)
    destination_ips = sorted({destination_ip for _, destination_ip, _, _ in websocket_candidates}, key=ipaddress.ip_address)
    return RedirectReceipt(
        interface=interface,
        listen_port=listen_port,
        source_ip=next(iter(sources)),
        destination_ips=destination_ips,
        requests=requests,
        captured_at="discovered",
        destination_port=next(iter(ports)),
    )


def parse_tcp_websocket(text: str, candidate: DiscoveryCandidate, *, listen_port: int) -> RedirectReceipt:
    _, source_ip = _validate_candidate(candidate)
    return _websocket_receipt(text, interface=candidate.interface, listen_port=listen_port, source_ip=source_ip)


def parse_passive_websocket(text: str, *, interface: str, local_addresses: set[str], listen_port: int) -> RedirectReceipt:
    if not local_addresses:
        raise ValueError("no_local_ipv4_addresses")
    return _websocket_receipt(text, interface=interface, listen_port=listen_port, destination_filter=local_addresses)


def discover_tcp(candidate: DiscoveryCandidate, *, listen_port: int, seconds: float = _DEFAULT_SECONDS) -> RedirectReceipt:
    return parse_tcp_websocket(capture_tcp(candidate, seconds), candidate, listen_port=listen_port)


def discover_existing_endpoint(*, interface: str, listen_port: int, seconds: float = _DEFAULT_SECONDS) -> RedirectReceipt | None:
    local_addresses = host_addresses()
    try:
        return parse_passive_websocket(
            capture_passive_tcp(interface, seconds),
            interface=interface,
            local_addresses=local_addresses,
            listen_port=listen_port,
        )
    except ValueError as exc:
        if str(exc) == "no_plaintext_websocket_upgrade":
            return None
        raise


def connected_chargers(data_dir: str | Path) -> list[str]:
    return [item.charger_id for item in appliance_status(data_dir).get("chargers", []) if item.connected]


def wait_for_charger(data_dir: str | Path, timeout: float, poll_interval: float = 0.5) -> str | None:
    if timeout < 0 or poll_interval <= 0:
        raise ValueError("invalid_wait_interval")
    deadline = time.monotonic() + timeout
    while True:
        chargers = connected_chargers(data_dir)
        if chargers:
            return chargers[0]
        if time.monotonic() >= deadline:
            return None
        time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))


def _state_path(state_dir: str | Path) -> Path:
    return Path(state_dir).expanduser() / _DISCOVERY_STATE


def _redirect_path(state_dir: str | Path) -> Path:
    return Path(state_dir).expanduser() / _REDIRECT_STATE


def _write_state(state_dir: str | Path, phase: str, candidate: DiscoveryCandidate | None = None, charger_id: str | None = None) -> None:
    path = _state_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"phase": phase, "charger_id": charger_id, "candidate": candidate.to_json() if candidate else None}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_redirect(state_dir: str | Path, receipt: RedirectReceipt) -> None:
    _redirect_path(state_dir).write_text(json.dumps(receipt.to_json(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _refuse_stale_files(state_dir: str | Path) -> None:
    root = Path(state_dir).expanduser()
    if any((root / name).exists() for name in (_DISCOVERY_STATE, _ADDRESS_STATE, _REDIRECT_STATE)):
        raise RuntimeError("discovery_state_exists")


def cleanup_discovery(state_dir: str | Path) -> None:
    require_root()
    root = Path(state_dir).expanduser()
    errors: list[str] = []
    if _redirect_path(root).exists():
        try:
            if redirect_tools.table_exists():
                redirect_tools.remove_redirect(root)
            else:
                _redirect_path(root).unlink()
        except (RuntimeError, ValueError) as exc:
            errors.append(str(exc))
    if _claim_path(root).exists():
        try:
            cleanup_address(root)
        except (RuntimeError, ValueError) as exc:
            errors.append(str(exc))
    _state_path(root).unlink(missing_ok=True)
    if errors:
        raise RuntimeError("; ".join(errors))


def run_discovery(
    *,
    data_dir: str | Path,
    state_dir: str | Path,
    interface: str = _DEFAULT_INTERFACE,
    listen_port: int = 9000,
    grace_seconds: float = 10.0,
    arp_seconds: float = _DEFAULT_SECONDS,
    tcp_seconds: float = _DEFAULT_SECONDS,
    connect_timeout: float = 30.0,
    min_requests: int = _MIN_REQUESTS,
    poll_interval: float = 0.5,
) -> DiscoveryResult:
    require_root()
    _refuse_stale_files(state_dir)
    existing = wait_for_charger(data_dir, grace_seconds, poll_interval)
    if existing:
        return DiscoveryResult("already_connected", existing)
    if redirect_tools.table_exists():
        raise RuntimeError("redirect_table_exists")

    address_claimed = False
    redirect_applied = False
    candidate: DiscoveryCandidate | None = None
    try:
        receipt = discover_existing_endpoint(interface=interface, listen_port=listen_port, seconds=tcp_seconds)
        if receipt is not None:
            _write_state(state_dir, "existing_endpoint")
        else:
            candidate = discover(interface=interface, seconds=arp_seconds, min_requests=min_requests)
            _write_state(state_dir, "candidate", candidate)
            claim_address(candidate, state_dir)
            address_claimed = True
            _write_state(state_dir, "address_claimed", candidate)
            receipt = discover_tcp(candidate, listen_port=listen_port, seconds=tcp_seconds)

        _write_redirect(state_dir, receipt)
        _write_state(state_dir, "redirect_ready", candidate)
        redirect_tools.apply_redirect(state_dir)
        redirect_applied = True
        _write_state(state_dir, "waiting_for_charger", candidate)
        charger_id = wait_for_charger(data_dir, connect_timeout, poll_interval)
        if not charger_id:
            raise RuntimeError("charger_connection_timeout")
        _write_state(state_dir, "connected", candidate, charger_id)
        return DiscoveryResult("connected", charger_id, candidate, receipt)
    except Exception:
        try:
            if redirect_applied or _redirect_path(state_dir).exists():
                if redirect_applied and redirect_tools.table_exists():
                    redirect_tools.remove_redirect(state_dir)
                else:
                    _redirect_path(state_dir).unlink(missing_ok=True)
            if address_claimed or _claim_path(state_dir).exists():
                cleanup_address(state_dir)
        finally:
            _state_path(state_dir).unlink(missing_ok=True)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m field.discover", description="Discover and bootstrap a charger-facing Ethernet endpoint.")
    subparsers = parser.add_subparsers(dest="command")
    run_parser = subparsers.add_parser("run", help="Run transactional discovery and preserve successful network state")
    run_parser.add_argument("--data-dir", required=True)
    run_parser.add_argument("--state-dir", required=True)
    run_parser.add_argument("--interface", default=_DEFAULT_INTERFACE)
    run_parser.add_argument("--listen-port", type=int, default=9000)
    run_parser.add_argument("--grace-seconds", type=float, default=10.0)
    run_parser.add_argument("--arp-seconds", type=float, default=_DEFAULT_SECONDS)
    run_parser.add_argument("--tcp-seconds", type=float, default=_DEFAULT_SECONDS)
    run_parser.add_argument("--connect-timeout", type=float, default=30.0)
    cleanup_parser = subparsers.add_parser("cleanup", help="Remove discovery-owned redirect and address state")
    cleanup_parser.add_argument("--state-dir", required=True)
    parser.add_argument("--interface", default=_DEFAULT_INTERFACE)
    parser.add_argument("--seconds", type=float, default=_DEFAULT_SECONDS)
    parser.add_argument("--min-requests", type=int, default=_MIN_REQUESTS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        try:
            result = run_discovery(
                data_dir=args.data_dir,
                state_dir=args.state_dir,
                interface=args.interface,
                listen_port=args.listen_port,
                grace_seconds=args.grace_seconds,
                arp_seconds=args.arp_seconds,
                tcp_seconds=args.tcp_seconds,
                connect_timeout=args.connect_timeout,
            )
        except (RuntimeError, ValueError) as exc:
            print(str(exc))
            return 1
        print(json.dumps(result.to_json(), indent=2, sort_keys=True))
        return 0
    if args.command == "cleanup":
        try:
            cleanup_discovery(args.state_dir)
        except (RuntimeError, ValueError) as exc:
            print(str(exc))
            return 1
        print("discovery state removed")
        return 0
    try:
        candidate = discover(interface=args.interface, seconds=args.seconds, min_requests=args.min_requests)
    except (RuntimeError, ValueError) as exc:
        print(str(exc))
        return 1
    print(json.dumps(candidate.to_json(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())