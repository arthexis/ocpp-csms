from __future__ import annotations

import re
import shutil
import subprocess
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from ocpp_discover import discover, persistence, provisional, redirect
from ocpp_discover.redirect import RedirectReceipt


class Configuration(NamedTuple):
    configured_matches: bool
    live_table_present: bool


@dataclass(frozen=True)
class DiscoveryEvidence:
    """Positive charger-side activity worth one bounded diagnostic attempt."""

    kind: str
    capture: str


_ARP_REQUEST = re.compile(
    r"ARP, Request who-has (?P<target>\d+\.\d+\.\d+\.\d+) tell (?P<source>\d+\.\d+\.\d+\.\d+)"
)
_ARP_REPLY = re.compile(r"ARP, Reply (?P<source>\d+\.\d+\.\d+\.\d+) is-at ")


def inspect_configuration(
    expected: RedirectReceipt,
    *,
    ruleset_path: str | Path = persistence.DEFAULT_RULESET_PATH,
) -> Configuration:
    """Return whether the owned persistent fragment matches and the live table exists."""
    path = Path(ruleset_path)
    configured_matches = (
        path.exists()
        and path.read_text(encoding="utf-8")
        == persistence.render_persistent_ruleset(expected)
    )
    return Configuration(configured_matches, redirect.table_exists())


def wait_for_discovery_evidence(interface: str, *, expected: RedirectReceipt | None = None) -> DiscoveryEvidence:
    """Block until positive charger-side evidence appears, without mutating the host.

    First contact requires two matching plaintext SYN attempts within 30 seconds;
    known adaptations still wake on a single SYN. ARP is a
    fallback: it must show the same non-self unresolved request at least twice.
    Established TCP traffic is excluded by BPF before Python sees it.
    """
    if not discover._INTERFACE.fullmatch(interface):
        raise ValueError("invalid_interface")
    if shutil.which("tcpdump") is None:
        raise RuntimeError("tcpdump_not_found")

    local_addresses = discover.host_addresses() if expected is None else set()
    packet_filter = "arp or (tcp[tcpflags] & (tcp-syn|tcp-ack) == tcp-syn)"
    command = ["tcpdump", "-p", "-i", interface, "-l", "-nn", packet_filter]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert process.stdout is not None
    captured: list[str] = []
    arp_counts: Counter[tuple[str, str]] = Counter()
    answered: set[str] = set()
    syn_seen: dict[tuple[str, str, int], tuple[float, str]] = {}
    try:
        for line in process.stdout:
            captured.append(line)
            tcp = discover._TCP_PACKET.search(line)
            if tcp is not None:
                # First contact only wakes for supported plaintext candidates.
                # Existing adaptations continue to observe replacement traffic.
                if expected is None:
                    port = int(tcp.group("dst_port"))
                    destination = tcp.group("dst")
                    supported = (port == 8888 and provisional.local_probe_enabled()) if destination in local_addresses else port == 80
                    if not supported or tcp.group("src") in local_addresses:
                        captured.pop()
                        continue
                source = tcp.group("src")
                if expected is None:
                    now = time.monotonic()
                    syn_seen = {key: value for key, value in syn_seen.items() if now - value[0] <= 30}
                    key = (source, tcp.group("dst"), int(tcp.group("dst_port")))
                    first = syn_seen.get(key)
                    if first is None:
                        if len(syn_seen) >= 256:
                            syn_seen.clear()
                        syn_seen[key] = (now, line)
                        captured.pop()
                        continue
                    return DiscoveryEvidence("candidate", first[1] + line)
                kind = "expected" if source == expected.source_ip else "candidate"
                return DiscoveryEvidence(kind, "".join(captured))

            reply = _ARP_REPLY.search(line)
            if reply is not None:
                answered.add(reply.group("source"))
                continue

            arp = _ARP_REQUEST.search(line)
            if arp is None:
                continue
            source = arp.group("source")
            target = arp.group("target")
            if source == target:
                continue
            arp_counts[(source, target)] += 1
            if arp_counts[(source, target)] < 2 or target in answered:
                continue
            kind = "expected" if expected is not None and source == expected.source_ip else "candidate"
            return DiscoveryEvidence(kind, "".join(captured))
        stderr = process.stderr.read().strip() if process.stderr is not None else ""
        detail = stderr.splitlines()[-1] if stderr else ""
        raise RuntimeError(detail or "passive_observer_stopped")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def observe_endpoint(interface: str, *, listen_port: int, seconds: float) -> RedirectReceipt | None:
    """Boundedly diagnose any plaintext WebSocket endpoint after a positive wake."""
    capture = discover.capture_passive_tcp(interface, seconds)
    try:
        return discover._websocket_receipt(capture, interface=interface, listen_port=listen_port)
    except ValueError as exc:
        if str(exc) in {"no_plaintext_websocket_upgrade", "secure_or_opaque_traffic"}:
            return None
        raise


def observe_contradiction(expected: RedirectReceipt, *, seconds: float) -> RedirectReceipt | None:
    """Passively return a different plaintext endpoint attempted by the expected charger."""
    observed = observe_endpoint(expected.interface, listen_port=expected.listen_port, seconds=seconds)
    if observed is None:
        return None
    return None if adaptation_identity(observed) == adaptation_identity(expected) else observed


def adaptation_identity(receipt: RedirectReceipt) -> tuple[object, ...]:
    requests = tuple(
        sorted(
            (request.destination_ip, request.host, request.path)
            for request in receipt.requests
        )
    )
    return (
        receipt.interface,
        receipt.source_ip,
        tuple(sorted(receipt.destination_ips)),
        receipt.destination_port,
        receipt.listen_port,
        requests,
    )
