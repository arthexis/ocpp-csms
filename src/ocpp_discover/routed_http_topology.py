"""Offline-only topology evidence contract for B2-B1.

No system calls, network inspection, or runtime permission grants live here.
Unverified facts must never be interpreted as proof of a dedicated interface.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InterfaceTopology:
    interface: str
    exists: bool
    kind: str
    physical: bool
    master: str | None
    lower_interfaces: tuple[str, ...]
    route_interfaces: tuple[str, ...]
    shared_subnet: bool
    non_charger_peers: bool


def dedicated_interface_verified(topology: InterfaceTopology) -> bool:
    """Evaluate independently supplied, trusted facts; do not collect them.

    B2-B1 DOES NOT integrate this result with routed HTTP authorization.
    Invalid or ambiguous evidence fails closed.
    """
    if type(topology) is not InterfaceTopology:
        return False
    if (type(topology.interface) is not str or not topology.interface
            or type(topology.exists) is not bool
            or type(topology.physical) is not bool
            or type(topology.kind) is not str
            or topology.master is not None
            or type(topology.lower_interfaces) is not tuple
            or type(topology.route_interfaces) is not tuple
            or type(topology.shared_subnet) is not bool
            or type(topology.non_charger_peers) is not bool):
        return False
    if any(type(item) is not str for item in topology.lower_interfaces + topology.route_interfaces):
        return False
    return (
        topology.exists
        and topology.physical
        and topology.kind == "ethernet"
        and not topology.lower_interfaces
        and topology.route_interfaces == (topology.interface,)
        and not topology.shared_subnet
        and not topology.non_charger_peers
    )
