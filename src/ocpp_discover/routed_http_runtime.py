"""Read-only configured-interface identity checks for routed TCP/80.

Physical peer exclusivity is neither inferred nor required. Authority is scoped
to an observed physical Ethernet interface and a pinned ifindex, checked again
before redirect. No host-mutating operations are performed here.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ocpp_discover.routed_http_policy import RoutedHttpPolicy
from ocpp_discover.routed_http_topology import InterfaceTopology

_SYS_NET = Path("/sys/class/net")
_PROC_ROUTE = Path("/proc/net/route")
_SEAL = object()


@dataclass(frozen=True)
class KernelObservation:
    interface: str
    ifindex: int
    topology: InterfaceTopology
    peer_exclusivity_proven: bool = False


@dataclass(frozen=True)
class EffectiveRoutedHttpAuthorization:
    """In-process capability; only this module can mint a valid seal."""
    interface: str
    ifindex: int
    _seal: object

    def authorizes(self, interface: str) -> bool:
        return self._seal is _SEAL and interface == self.interface and self.ifindex > 0


def collect_kernel_observation(interface: str) -> KernelObservation | None:
    """Read local interface facts; no external-peer conclusions or mutations."""
    if not interface or "/" in interface or interface in {".", ".."}:
        return None
    directory = _SYS_NET / interface
    try:
        index = int((directory / "ifindex").read_text().strip())
        iflink = int((directory / "iflink").read_text().strip())
        kind = int((directory / "type").read_text().strip())
        operational = (directory / "operstate").read_text().strip()
        names = tuple(path.name for path in directory.iterdir())
        physical = (directory / "device").exists()
        with _PROC_ROUTE.open(encoding="ascii") as stream:
            rows = [line.split() for line in stream.readlines()[1:]]
        routes = tuple(sorted({row[0] for row in rows if len(row) >= 8}))
    except (OSError, ValueError, UnicodeError):
        return None
    if index <= 0 or iflink != index or operational not in {"up", "unknown"}:
        return None
    related = tuple(name for name in names if name.startswith(("lower_", "upper_")))
    topology = InterfaceTopology(
        interface=interface, exists=True,
        kind="ethernet" if kind == 1 else "other",
        physical=physical and not related,
        master="attached" if "master" in names else None,
        lower_interfaces=related,
        route_interfaces=routes,
        shared_subnet=True,  # Unknown without verified peer/subnet ownership.
        non_charger_peers=True,  # Kernel cannot prove all external peers are chargers.
    )
    return KernelObservation(interface, index, topology)


def authorize_routed_http(policy: RoutedHttpPolicy, interface: str) -> EffectiveRoutedHttpAuthorization | None:
    """Authorize only the configured, observed physical Ethernet interface."""
    if type(policy) is not RoutedHttpPolicy or not policy.authorizes(interface):
        return None
    observed = collect_kernel_observation(interface)
    if (observed is None or observed.interface != interface or observed.ifindex <= 0
            or not observed.topology.physical or observed.topology.kind != "ethernet"
            or observed.topology.master is not None):
        return None
    return EffectiveRoutedHttpAuthorization(interface, observed.ifindex, _SEAL)


def still_authorized(auth: EffectiveRoutedHttpAuthorization | None, interface: str) -> bool:
    """Reinspect immediately before applying a provisional TCP/80 redirect."""
    if type(auth) is not EffectiveRoutedHttpAuthorization or not auth.authorizes(interface):
        return False
    observed = collect_kernel_observation(interface)
    return bool(observed is not None and observed.ifindex == auth.ifindex
                and observed.interface == interface and observed.topology.physical
                and observed.topology.kind == "ethernet"
                and observed.topology.master is None)
