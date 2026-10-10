from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from ocpp_discover import redirect as redirect_tools
from ocpp_discover.redirect import RedirectReceipt, WebSocketRequest
from ocpp_discover.models import AddressClaim, DiscoveryCandidate, DiscoveryResult
from ocpp_discover.arp import discover_candidate
from ocpp_discover.cli_parser import build_parser
from ocpp_discover.websocket import _TCP_PACKET, _validate_candidate, _tcp_blocks, _websocket_receipt, parse_tcp_websocket, parse_passive_websocket
from ocpp_csms.status import appliance_status

_DEFAULT_INTERFACE = "eth0"
_DEFAULT_SECONDS = 15.0
_MIN_REQUESTS = 2
_ADDRESS_STATE = "address.json"
_DISCOVERY_STATE = "discovery.json"
_REDIRECT_STATE = "redirect.json"

_INTERFACE = re.compile(r"^[A-Za-z0-9_.:-]+$")
_MAC = re.compile(r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$", re.IGNORECASE)


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


def capture_tcp(candidate: DiscoveryCandidate, seconds: float) -> str:
    source_mac, source_ip = _validate_candidate(candidate)
    packet_filter = f"ether src {source_mac} and ip src {source_ip} and tcp"
    return _bounded_tcpdump(["tcpdump", "-i", candidate.interface, "-l", "-nn", "-s0", "-A", packet_filter], seconds)


def capture_passive_tcp(interface: str, seconds: float) -> str:
    if not _INTERFACE.fullmatch(interface):
        raise ValueError("invalid_interface")
    return _bounded_tcpdump(["tcpdump", "-i", interface, "-l", "-nn", "-s0", "-A", "tcp"], seconds)


def _write_capture_log(path: str | Path, capture: str) -> None:
    """Preserve a diagnostic capture without overwriting an earlier result."""
    destination = Path(path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("x", encoding="utf-8") as handle:
            handle.write(capture)
    except FileExistsError:
        raise RuntimeError("capture_log_exists") from None


def discover_tcp(candidate: DiscoveryCandidate, *, listen_port: int, seconds: float = _DEFAULT_SECONDS) -> RedirectReceipt:
    return parse_tcp_websocket(capture_tcp(candidate, seconds), candidate, listen_port=listen_port)


def discover_existing_endpoint(
    *,
    interface: str,
    listen_port: int,
    seconds: float = _DEFAULT_SECONDS,
    capture_log: str | Path | None = None,
) -> RedirectReceipt | None:
    local_addresses = host_addresses()
    capture = capture_passive_tcp(interface, seconds)
    if capture_log is not None:
        _write_capture_log(capture_log, capture)
    try:
        return parse_passive_websocket(
            capture,
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
    existing_endpoint_only: bool = False,
    passive_diagnostic_only: bool = False,
    force_passive_capture: bool = False,
    passive_capture_log: str | Path | None = None,
) -> DiscoveryResult:
    require_root()
    _refuse_stale_files(state_dir)
    if not force_passive_capture:
        existing = wait_for_charger(data_dir, grace_seconds, poll_interval)
        if existing:
            return DiscoveryResult("already_connected", existing)
    if redirect_tools.table_exists():
        raise RuntimeError("redirect_table_exists")

    address_claimed = False
    redirect_applied = False
    candidate: DiscoveryCandidate | None = None
    try:
        receipt = discover_existing_endpoint(
            interface=interface,
            listen_port=listen_port,
            seconds=tcp_seconds,
            capture_log=passive_capture_log,
        )
        if receipt is not None:
            if passive_diagnostic_only:
                return DiscoveryResult("existing_endpoint_observed", None, None, receipt)
            _write_state(state_dir, "existing_endpoint")
        else:
            if existing_endpoint_only or passive_diagnostic_only:
                raise RuntimeError("no_existing_endpoint_websocket_upgrade")
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
                existing_endpoint_only=args.existing_endpoint_only,
                passive_diagnostic_only=args.passive_diagnostic_only,
                force_passive_capture=args.force_passive_capture,
                passive_capture_log=args.passive_capture_log,
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
