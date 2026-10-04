from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

_PACKET = re.compile(
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
_TABLE = "ocpp_field_redirect"


@dataclass(frozen=True)
class WebSocketRequest:
    destination_ip: str
    host: str
    path: str


@dataclass(frozen=True)
class RedirectReceipt:
    interface: str
    listen_port: int
    source_ip: str
    destination_ips: list[str]
    requests: list[WebSocketRequest]
    captured_at: str

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def _packet_blocks(text: str) -> list[tuple[re.Match[str], str]]:
    matches = list(_PACKET.finditer(text))
    return [
        (match, text[match.end() : matches[index + 1].start() if index + 1 < len(matches) else len(text)])
        for index, match in enumerate(matches)
    ]


def parse_capture(text: str, *, interface: str, listen_port: int, captured_at: str | None = None) -> RedirectReceipt:
    candidates: list[tuple[str, WebSocketRequest]] = []
    saw_tls_port = False

    for packet, payload in _packet_blocks(text):
        destination_port = int(packet.group("dst_port"))
        if destination_port == 443:
            saw_tls_port = True
            continue
        if destination_port != 80:
            continue

        get = _GET.search(payload)
        host = _HOST.search(payload)
        connection = _CONNECTION.search(payload)
        if not (get and host and _UPGRADE.search(payload) and connection):
            continue
        if "upgrade" not in {token.strip().lower() for token in connection.group("value").split(",")}:
            continue

        candidates.append(
            (
                packet.group("src"),
                WebSocketRequest(
                    destination_ip=packet.group("dst"),
                    host=host.group("host"),
                    path=get.group("path"),
                ),
            )
        )

    if not candidates:
        if saw_tls_port:
            raise ValueError("secure_or_opaque_traffic")
        raise ValueError("no_plaintext_websocket_upgrade")

    sources = {source for source, _ in candidates}
    if len(sources) != 1:
        raise ValueError("ambiguous_websocket_sources")

    source_ip = next(iter(sources))
    requests: list[WebSocketRequest] = []
    seen_requests: set[tuple[str, str, str]] = set()
    for _, request in candidates:
        key = (request.destination_ip, request.host, request.path)
        if key not in seen_requests:
            seen_requests.add(key)
            requests.append(request)

    destinations = sorted({request.destination_ip for request in requests})
    return RedirectReceipt(
        interface=interface,
        listen_port=listen_port,
        source_ip=source_ip,
        destination_ips=destinations,
        requests=requests,
        captured_at=captured_at or datetime.now(timezone.utc).isoformat(),
    )


def listener_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def capture_text(interface: str, seconds: float) -> str:
    if shutil.which("tcpdump") is None:
        raise RuntimeError("tcpdump_not_found")

    command = [
        "tcpdump",
        "-i",
        interface,
        "-l",
        "-nn",
        "-s0",
        "-A",
        "tcp dst port 80 or tcp dst port 443",
    ]
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


def capture(interface: str, listen_port: int, seconds: float, run_dir: str | Path) -> RedirectReceipt:
    if seconds <= 0:
        raise ValueError("seconds_must_be_positive")
    if not 1 <= listen_port <= 65535:
        raise ValueError("invalid_listen_port")
    if not listener_available(listen_port):
        raise RuntimeError("listener_unavailable")

    directory = Path(run_dir).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    receipt_path = directory / "redirect.json"
    if receipt_path.exists():
        raise RuntimeError("redirect_receipt_exists")

    receipt = parse_capture(capture_text(interface, seconds), interface=interface, listen_port=listen_port)
    receipt_path.write_text(json.dumps(receipt.to_json(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def receipt_from_json(payload: object) -> RedirectReceipt:
    if not isinstance(payload, dict):
        raise ValueError("invalid_redirect_receipt")
    try:
        requests_payload = payload["requests"]
        destinations_payload = payload["destination_ips"]
        if not isinstance(requests_payload, list) or not isinstance(destinations_payload, list):
            raise TypeError
        requests = [
            WebSocketRequest(
                destination_ip=str(request["destination_ip"]),
                host=str(request["host"]),
                path=str(request["path"]),
            )
            for request in requests_payload
            if isinstance(request, dict)
        ]
        if len(requests) != len(requests_payload):
            raise TypeError
        receipt = RedirectReceipt(
            interface=str(payload["interface"]),
            listen_port=int(payload["listen_port"]),
            source_ip=str(payload["source_ip"]),
            destination_ips=[str(item) for item in destinations_payload],
            requests=requests,
            captured_at=str(payload["captured_at"]),
        )
    except (KeyError, TypeError, ValueError):
        raise ValueError("invalid_redirect_receipt") from None
    validate_receipt(receipt)
    return receipt


def load_receipt(run_dir: str | Path) -> RedirectReceipt:
    path = Path(run_dir).expanduser() / "redirect.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RuntimeError("redirect_receipt_not_found") from None
    except json.JSONDecodeError:
        raise ValueError("invalid_redirect_receipt") from None
    return receipt_from_json(payload)


def _ipv4(value: str, error: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise ValueError(error) from None
    if address.version != 4:
        raise ValueError(error)
    return str(address)


def validate_receipt(receipt: RedirectReceipt) -> None:
    if not _INTERFACE.fullmatch(receipt.interface):
        raise ValueError("invalid_interface")
    if not 1 <= receipt.listen_port <= 65535:
        raise ValueError("invalid_listen_port")
    _ipv4(receipt.source_ip, "invalid_source_ip")
    if not receipt.destination_ips:
        raise ValueError("no_destination_ips")
    if not receipt.requests:
        raise ValueError("no_websocket_requests")
    normalized = [_ipv4(destination, "invalid_destination_ip") for destination in receipt.destination_ips]
    if len(set(normalized)) != len(normalized):
        raise ValueError("duplicate_destination_ip")
    request_destinations = {_ipv4(request.destination_ip, "invalid_request_destination_ip") for request in receipt.requests}
    if request_destinations != set(normalized):
        raise ValueError("destination_evidence_mismatch")


def render_ruleset(receipt: RedirectReceipt) -> str:
    validate_receipt(receipt)
    destinations = ", ".join(sorted(receipt.destination_ips, key=ipaddress.ip_address))
    return (
        f"table ip {_TABLE} {{\n"
        "  chain prerouting {\n"
        "    type nat hook prerouting priority dstnat; policy accept;\n"
        f'    iifname "{receipt.interface}" ip saddr {receipt.source_ip} '
        f"ip daddr {{ {destinations} }} tcp dport 80 redirect to :{receipt.listen_port}\n"
        "  }\n"
        "}\n"
    )


def _require_nft() -> None:
    if shutil.which("nft") is None:
        raise RuntimeError("nft_not_found")


def _run_nft(command: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    _require_nft()
    return subprocess.run(
        command,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )


def _nft_error(result: subprocess.CompletedProcess[str], fallback: str) -> RuntimeError:
    detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else fallback
    return RuntimeError(detail)


def validate_ruleset(receipt: RedirectReceipt) -> str:
    ruleset = render_ruleset(receipt)
    result = _run_nft(["nft", "-c", "-f", "-"], input_text=ruleset)
    if result.returncode != 0:
        raise _nft_error(result, "nft_validation_failed")
    return ruleset


def require_root() -> None:
    if os.geteuid() != 0:
        raise RuntimeError("root_required")


def table_exists() -> bool:
    result = _run_nft(["nft", "list", "table", "ip", _TABLE])
    if result.returncode == 0:
        return True
    stderr = result.stderr.lower()
    if "no such file or directory" in stderr or "not found" in stderr:
        return False
    raise _nft_error(result, "nft_table_status_failed")


def apply_redirect(run_dir: str | Path) -> str:
    require_root()
    receipt = load_receipt(run_dir)
    if not listener_available(receipt.listen_port):
        raise RuntimeError("listener_unavailable")
    if table_exists():
        raise RuntimeError("redirect_table_exists")

    ruleset = validate_ruleset(receipt)
    result = _run_nft(["nft", "-f", "-"], input_text=ruleset)
    if result.returncode != 0:
        raise _nft_error(result, "nft_apply_failed")
    return ruleset


def remove_redirect(run_dir: str | Path) -> None:
    require_root()
    load_receipt(run_dir)
    if not table_exists():
        raise RuntimeError("redirect_table_not_found")
    result = _run_nft(["nft", "delete", "table", "ip", _TABLE])
    if result.returncode != 0:
        raise _nft_error(result, "nft_remove_failed")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m field.redirect",
        description="Field discovery, validation, and explicit temporary redirects for plaintext OCPP WebSockets.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    capture_parser = subparsers.add_parser("capture", help="Observe a bounded passive capture and write redirect.json")
    capture_parser.add_argument("--interface", required=True)
    capture_parser.add_argument("--listen-port", type=int, required=True)
    capture_parser.add_argument("--seconds", type=float, required=True)
    capture_parser.add_argument("--run-dir", required=True)
    validate_parser = subparsers.add_parser("validate", help="Render redirect.json as nftables rules and check with nft -c")
    validate_parser.add_argument("run_dir")
    apply_parser = subparsers.add_parser("apply", help="Install the validated temporary redirect table")
    apply_parser.add_argument("run_dir")
    remove_parser = subparsers.add_parser("remove", help="Delete only the temporary redirect table")
    remove_parser.add_argument("run_dir")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "capture":
        try:
            receipt = capture(args.interface, args.listen_port, args.seconds, args.run_dir)
        except (RuntimeError, ValueError) as exc:
            print(str(exc))
            return 1
        print(json.dumps(receipt.to_json(), indent=2, sort_keys=True))
        return 0
    if args.command == "validate":
        try:
            ruleset = validate_ruleset(load_receipt(args.run_dir))
        except (RuntimeError, ValueError) as exc:
            print(str(exc))
            return 1
        print(ruleset, end="")
        return 0
    if args.command == "apply":
        try:
            ruleset = apply_redirect(args.run_dir)
        except (RuntimeError, ValueError) as exc:
            print(str(exc))
            return 1
        print(ruleset, end="")
        return 0
    if args.command == "remove":
        try:
            remove_redirect(args.run_dir)
        except (RuntimeError, ValueError) as exc:
            print(str(exc))
            return 1
        print(f"removed table ip {_TABLE}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
