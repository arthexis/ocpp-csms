"""Observation-only TLS ClientHello discovery from bounded packet captures.

TLS is not proof of WSS or OCPP. Never change the network from these observations.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import struct
import subprocess
import tempfile
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class TLSCandidate:
    source_ip: str
    source_port: int
    destination_ip: str
    destination_port: int
    server_name: str | None
    alpn: tuple[str, ...]
    offered_versions: tuple[str, ...]
    classification: str = "tls_candidate"


def _u16(data: bytes, offset: int) -> int:
    if offset + 2 > len(data):
        raise ValueError("truncated_tls")
    return int.from_bytes(data[offset:offset + 2], "big")


def _vector(data: bytes, offset: int, length_bytes: int) -> tuple[bytes, int]:
    if offset + length_bytes > len(data):
        raise ValueError("truncated_tls")
    size = int.from_bytes(data[offset:offset + length_bytes], "big")
    start = offset + length_bytes
    end = start + size
    if end > len(data):
        raise ValueError("truncated_tls")
    return data[start:end], end


def parse_client_hello(handshake: bytes) -> tuple[str | None, tuple[str, ...], tuple[str, ...]]:
    """Parse a complete handshake message (type + 24-bit length + body)."""
    if len(handshake) < 4 or handshake[0] != 1:
        raise ValueError("not_client_hello")
    size = int.from_bytes(handshake[1:4], "big")
    if size > 65536 or len(handshake) < size + 4:
        raise ValueError("truncated_tls")
    body = handshake[4:4 + size]
    if len(body) < 34:
        raise ValueError("truncated_tls")
    legacy_version = body[:2]
    pos = 34  # version and random
    _, pos = _vector(body, pos, 1)  # session ID
    _, pos = _vector(body, pos, 2)  # cipher suites
    _, pos = _vector(body, pos, 1)  # compression
    sni = None
    alpn: list[str] = []
    versions = [f"0x{legacy_version.hex()}"]
    if pos == len(body):
        return sni, tuple(alpn), tuple(versions)
    extensions, pos = _vector(body, pos, 2)
    if pos != len(body):
        raise ValueError("invalid_tls_extensions")
    offset = 0
    while offset < len(extensions):
        kind = _u16(extensions, offset)
        ext, offset = _vector(extensions, offset + 2, 2)
        if kind == 0:  # server_name
            names, end = _vector(ext, 0, 2)
            if end != len(ext):
                raise ValueError("invalid_sni")
            i = 0
            while i < len(names):
                if i + 3 > len(names):
                    raise ValueError("invalid_sni")
                name_type = names[i]
                name, i = _vector(names, i + 1, 2)
                if name_type == 0 and sni is None:
                    try:
                        sni = name.decode("ascii").lower()
                        if not sni or len(sni) > 253 or any(ord(c) < 33 for c in sni):
                            raise ValueError("invalid_sni")
                    except UnicodeDecodeError:
                        raise ValueError("invalid_sni") from None
        elif kind == 16:  # ALPN
            protocols, end = _vector(ext, 0, 2)
            if end != len(ext):
                raise ValueError("invalid_alpn")
            i = 0
            while i < len(protocols):
                protocol, i = _vector(protocols, i, 1)
                alpn.append(protocol.decode("ascii", errors="replace"))
        elif kind == 43:  # supported_versions
            offered, end = _vector(ext, 0, 1)
            if end != len(ext) or len(offered) % 2:
                raise ValueError("invalid_versions")
            versions = [f"0x{offered[i:i+2].hex()}" for i in range(0, len(offered), 2)]
    return sni, tuple(alpn), tuple(versions)


def _hello_from_stream(data: bytes) -> tuple[str | None, tuple[str, ...], tuple[str, ...]] | None:
    """Join TLS handshake fragments spanning multiple TLS records."""
    handshake = bytearray()
    offset = 0
    while offset + 5 <= len(data):
        content_type, major, _minor = data[offset:offset + 3]
        if major != 3 or content_type != 22:
            return None
        size = _u16(data, offset + 3)
        if size > 18432 or offset + 5 + size > len(data):
            return None
        handshake.extend(data[offset + 5:offset + 5 + size])
        if len(handshake) >= 4:
            length = int.from_bytes(handshake[1:4], "big")
            if handshake[0] != 1 or length > 65536:
                return None
            if len(handshake) >= 4 + length:
                try:
                    return parse_client_hello(bytes(handshake[:4 + length]))
                except ValueError:
                    return None
        offset += 5 + size
    return None


def _packets(raw: bytes):
    """Yield Ethernet/IPv4 TCP payloads from classic tcpdump pcap, not pcapng."""
    if len(raw) < 24:
        return
    magic = raw[:4]
    endian = "<" if magic in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1") else ">"
    if magic not in (b"\xd4\xc3\xb2\xa1", b"\xa1\xb2\xc3\xd4", b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d"):
        raise ValueError("unsupported_capture_format")
    link_type = struct.unpack_from(endian + "I", raw, 20)[0]
    if link_type != 1:
        raise ValueError("ethernet_capture_required")
    offset = 24
    while offset + 16 <= len(raw):
        _sec, _usec, captured, _original = struct.unpack_from(endian + "IIII", raw, offset)
        offset += 16
        if offset + captured > len(raw):
            break
        frame = raw[offset:offset + captured]
        offset += captured
        if len(frame) < 14:
            continue
        ethertype = int.from_bytes(frame[12:14], "big")
        ip_start = 14
        if ethertype in (0x8100, 0x88a8) and len(frame) >= 18:
            ethertype = int.from_bytes(frame[16:18], "big")
            ip_start = 18
        if ethertype != 0x0800 or len(frame) < ip_start + 20:
            continue
        version_ihl = frame[ip_start]
        ihl = (version_ihl & 15) * 4
        if version_ihl >> 4 != 4 or ihl < 20 or len(frame) < ip_start + ihl or frame[ip_start + 9] != 6:
            continue
        # Fragmented IPv4 datagrams cannot be parsed without IP reassembly.
        if int.from_bytes(frame[ip_start + 6:ip_start + 8], "big") & 0x3fff:
            continue
        total = int.from_bytes(frame[ip_start + 2:ip_start + 4], "big")
        end = min(len(frame), ip_start + total)
        tcp_start = ip_start + ihl
        if tcp_start + 20 > end:
            continue
        header = (frame[tcp_start + 12] >> 4) * 4
        if header < 20 or tcp_start + header > end:
            continue
        src = str(ipaddress.IPv4Address(frame[ip_start + 12:ip_start + 16]))
        dst = str(ipaddress.IPv4Address(frame[ip_start + 16:ip_start + 20]))
        sport, dport = struct.unpack_from("!HH", frame, tcp_start)
        seq = struct.unpack_from("!I", frame, tcp_start + 4)[0]
        payload = frame[tcp_start + header:end]
        if payload:
            yield (src, sport, dst, dport), seq, payload


def observe_pcap(raw: bytes) -> list[TLSCandidate]:
    segments = defaultdict(dict)
    for flow, seq, payload in _packets(raw):
        segments[flow].setdefault(seq, payload)
    results = []
    for (src, sport, dst, dport), parts in segments.items():
        # Assemble in sequence order, stopping at gaps; out-of-order packets are fine.
        ordered = sorted(parts.items())
        for index, (start, payload) in enumerate(ordered):
            if not payload.startswith(b"\x16\x03"):
                continue
            stream = bytearray(payload)
            expected = start + len(payload)
            for seq, fragment in ordered[index + 1:]:
                if seq > expected:
                    break
                overlap = expected - seq
                if overlap < len(fragment):
                    stream.extend(fragment[overlap:])
                    expected += len(fragment) - overlap
                if len(stream) > 131072:
                    break
            hello = _hello_from_stream(bytes(stream))
            if hello is not None:
                sni, alpn, versions = hello
                results.append(TLSCandidate(src, sport, dst, dport, sni, alpn, versions))
                break
    return results


def capture(interface: str, seconds: float) -> list[TLSCandidate]:
    """Capture bounded TCP traffic; never send packets or change host configuration."""
    from ocpp_discover.discover import _INTERFACE
    if not _INTERFACE.fullmatch(interface) or seconds <= 0:
        raise ValueError("invalid_capture_options")
    with tempfile.TemporaryDirectory(prefix="ocpp-discover-tls-") as directory:
        path = Path(directory) / "capture.pcap"
        command = ["tcpdump", "-i", interface, "-s", "0", "-U", "-w", str(path), "tcp"]
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        try:
            try:
                process.communicate(timeout=seconds)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
            if process.returncode not in (0, -15):
                raise RuntimeError("tcp_capture_failed")
            return observe_pcap(path.read_bytes())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Observe TLS ClientHello endpoints (not confirmed WSS/OCPP).")
    parser.add_argument("--interface", default="eth0")
    parser.add_argument("--seconds", type=float, default=15.0)
    parser.add_argument("--pcap", help="Analyze a saved classic Ethernet pcap instead of capturing live traffic")
    args = parser.parse_args(argv)
    try:
        results = observe_pcap(Path(args.pcap).read_bytes()) if args.pcap else capture(args.interface, args.seconds)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"{exc}\\n")
    print(json.dumps([asdict(item) for item in results], indent=2))
    return 0
