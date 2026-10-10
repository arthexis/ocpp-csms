"""Pure TCP packet and plaintext WebSocket upgrade parsing."""

from __future__ import annotations

import ipaddress
import re

from ocpp_discover.models import DiscoveryCandidate
from ocpp_discover.redirect import RedirectReceipt, WebSocketRequest

_INTERFACE = re.compile(r"^[A-Za-z0-9_.:-]+$")
_MAC = re.compile(r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$", re.IGNORECASE)

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


