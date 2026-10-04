from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from collections import Counter
from dataclasses import asdict, dataclass

_DEFAULT_INTERFACE = "eth0"
_DEFAULT_SECONDS = 15.0
_MIN_REQUESTS = 2

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


@dataclass(frozen=True)
class DiscoveryCandidate:
    interface: str
    source_mac: str
    source_ip: str
    target_ip: str
    requests: int

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def capture_arp(interface: str, seconds: float) -> str:
    if seconds <= 0:
        raise ValueError("seconds_must_be_positive")
    if shutil.which("tcpdump") is None:
        raise RuntimeError("tcpdump_not_found")

    command = ["tcpdump", "-i", interface, "-l", "-nn", "-e", "arp"]
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


def discover_candidate(
    text: str,
    *,
    interface: str = _DEFAULT_INTERFACE,
    min_requests: int = _MIN_REQUESTS,
) -> DiscoveryCandidate:
    if min_requests < 1:
        raise ValueError("min_requests_must_be_positive")

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

        counts[
            (
                request.group("src_mac").lower(),
                source_ip,
                target_ip,
            )
        ] += 1

    candidates = [
        DiscoveryCandidate(
            interface=interface,
            source_mac=source_mac,
            source_ip=source_ip,
            target_ip=target_ip,
            requests=count,
        )
        for (source_mac, source_ip, target_ip), count in counts.items()
        if count >= min_requests and target_ip not in answered
    ]

    if not candidates:
        raise ValueError("no_unresolved_arp_candidate")
    if len(candidates) != 1:
        raise ValueError("ambiguous_arp_candidates")
    return candidates[0]


def discover(
    *,
    interface: str = _DEFAULT_INTERFACE,
    seconds: float = _DEFAULT_SECONDS,
    min_requests: int = _MIN_REQUESTS,
) -> DiscoveryCandidate:
    return discover_candidate(
        capture_arp(interface, seconds),
        interface=interface,
        min_requests=min_requests,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m field.discover",
        description="Passively identify one repeated unresolved ARP target from a charger-facing Ethernet interface.",
    )
    parser.add_argument("--interface", default=_DEFAULT_INTERFACE)
    parser.add_argument("--seconds", type=float, default=_DEFAULT_SECONDS)
    parser.add_argument("--min-requests", type=int, default=_MIN_REQUESTS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        candidate = discover(
            interface=args.interface,
            seconds=args.seconds,
            min_requests=args.min_requests,
        )
    except (RuntimeError, ValueError) as exc:
        print(str(exc))
        return 1
    print(json.dumps(candidate.to_json(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
