from __future__ import annotations

import ipaddress
from dataclasses import dataclass

from ocpp_discover import discover, redirect


@dataclass(frozen=True)
class LocalSynCandidate:
    interface: str
    source_ip: str
    destination_ip: str
    destination_port: int
    listen_port: int


def local_syn_candidate(text: str, *, interface: str, listen_port: int) -> LocalSynCandidate | None:
    """Return one unambiguous SYN to a host-local non-listener endpoint."""
    if not discover._INTERFACE.fullmatch(interface):
        raise ValueError("invalid_interface")
    if not 1 <= listen_port <= 65535:
        raise ValueError("invalid_listen_port")

    packets = list(discover._TCP_PACKET.finditer(text))
    if not packets:
        return None
    try:
        local_addresses = discover.host_addresses()
    except RuntimeError as exc:
        if str(exc) == "ip_not_found":
            return None
        raise

    candidates: set[tuple[str, str, int]] = set()
    for packet in packets:
        source = packet.group("src")
        destination = packet.group("dst")
        destination_port = int(packet.group("dst_port"))
        if destination not in local_addresses or source in local_addresses:
            continue
        if destination_port == listen_port:
            continue
        candidates.add((source, destination, destination_port))

    if not candidates:
        return None
    if len(candidates) != 1:
        raise ValueError("ambiguous_local_syn_candidates")

    source, destination, destination_port = next(iter(candidates))
    source_address = ipaddress.ip_address(source)
    destination_address = ipaddress.ip_address(destination)
    if source_address.version != 4 or destination_address.version != 4:
        raise ValueError("local_syn_requires_ipv4")
    return LocalSynCandidate(interface, source, destination, destination_port, listen_port)


def _ruleset(candidate: LocalSynCandidate) -> str:
    return (
        "table ip ocpp_field_redirect {\n"
        "  chain prerouting {\n"
        "    type nat hook prerouting priority dstnat; policy accept;\n"
        f'    iifname "{candidate.interface}" ip saddr {candidate.source_ip} '
        f"ip daddr {candidate.destination_ip} tcp dport {candidate.destination_port} "
        f"redirect to :{candidate.listen_port}\n"
        "  }\n"
        "}\n"
    )


def apply(candidate: LocalSynCandidate) -> str:
    """Install an exact-match temporary redirect derived only from one observed SYN."""
    redirect.require_root()
    if not redirect.listener_available(candidate.listen_port):
        raise RuntimeError("listener_unavailable")
    if redirect.table_exists():
        raise RuntimeError("redirect_table_exists")

    ruleset = _ruleset(candidate)
    checked = redirect._run_nft(["nft", "-c", "-f", "-"], input_text=ruleset)
    if checked.returncode != 0:
        raise redirect._nft_error(checked, "nft_validation_failed")
    applied = redirect._run_nft(["nft", "-f", "-"], input_text=ruleset)
    if applied.returncode != 0:
        raise redirect._nft_error(applied, "nft_apply_failed")
    return ruleset


def remove() -> None:
    """Remove only Discover's temporary table; callers must establish ownership first."""
    redirect.require_root()
    if not redirect.table_exists():
        return
    result = redirect._run_nft(["nft", "delete", "table", "ip", "ocpp_field_redirect"])
    if result.returncode != 0:
        raise redirect._nft_error(result, "nft_remove_failed")


def matches(candidate: LocalSynCandidate, receipt: redirect.RedirectReceipt) -> bool:
    return (
        receipt.interface == candidate.interface
        and receipt.source_ip == candidate.source_ip
        and receipt.destination_ips == [candidate.destination_ip]
        and receipt.destination_port == candidate.destination_port
        and receipt.listen_port == candidate.listen_port
    )
