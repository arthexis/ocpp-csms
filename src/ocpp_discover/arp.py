"""Pure ARP candidate identification from captured packet text."""

from __future__ import annotations

import re
from collections import Counter

from ocpp_discover.models import DiscoveryCandidate

_DEFAULT_INTERFACE = "eth0"
_MIN_REQUESTS = 2
_INTERFACE = re.compile(r"^[A-Za-z0-9_.:-]+$")

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


