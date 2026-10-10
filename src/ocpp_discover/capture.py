"""Bounded ARP and TCP packet capture for charger discovery."""
from __future__ import annotations

import re
import shutil
import subprocess

from ocpp_discover.models import DiscoveryCandidate
from ocpp_discover.websocket import _validate_candidate

_INTERFACE = re.compile(r"^[A-Za-z0-9_.:-]+$")

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


def capture_tcp(candidate: DiscoveryCandidate, seconds: float) -> str:
    source_mac, source_ip = _validate_candidate(candidate)
    packet_filter = f"ether src {source_mac} and ip src {source_ip} and tcp"
    return _bounded_tcpdump(["tcpdump", "-i", candidate.interface, "-l", "-nn", "-s0", "-A", packet_filter], seconds)


def capture_passive_tcp(interface: str, seconds: float) -> str:
    if not _INTERFACE.fullmatch(interface):
        raise ValueError("invalid_interface")
    return _bounded_tcpdump(["tcpdump", "-i", interface, "-l", "-nn", "-s0", "-A", "tcp"], seconds)

