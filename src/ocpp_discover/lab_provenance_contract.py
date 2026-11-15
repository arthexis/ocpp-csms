"""B2-D1: pure, advisory lab provenance assessment for Discover (#355).

Inputs are caller-supplied assertions, not trusted field attestations.
This module never mints persistence authority or performs host I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from ipaddress import IPv4Address, IPv4Network
import re

from ocpp_discover.recovery_contract import Origin


_LAB_NETWORKS = tuple(IPv4Network(value) for value in (
    "198.18.0.0/15",       # Benchmark / isolated test destinations
    "192.0.2.0/24",        # TEST-NET-1
    "198.51.100.0/24",     # TEST-NET-2
    "203.0.113.0/24",      # TEST-NET-3
))
_INTERFACE = re.compile(r"[A-Za-z0-9_.:-]+\Z")
_TOKEN = re.compile(r"\S+\Z")


def _text(value: object, reason: str) -> str:
    if type(value) is not str or not value or _TOKEN.fullmatch(value) is None:
        raise ValueError(reason)
    return value


def _ipv4(value: object, reason: str) -> str:
    if type(value) is not str:
        raise ValueError(reason)
    try:
        address = IPv4Address(value)
    except (ValueError, TypeError):
        raise ValueError(reason) from None
    if (str(address) != value or address.is_unspecified or address.is_multicast
            or address.is_loopback or address.is_link_local):
        raise ValueError(reason)
    return value


def _port(value: object, reason: str) -> int:
    if type(value) is not int or not 1 <= value <= 65535:
        raise ValueError(reason)
    return value


def _inventory(values: object, *, ipv4: bool) -> None:
    if type(values) is not tuple:
        raise ValueError("invalid_synthetic_inventory")
    for value in values:
        if ipv4:
            _ipv4(value, "invalid_synthetic_inventory")
        else:
            _text(value, "invalid_synthetic_inventory")
    if len(set(values)) != len(values):
        raise ValueError("duplicate_synthetic_inventory")


class IsolationDecision(str, Enum):
    LAB_ONLY = "lab_only"
    DENY_NEW_PERSISTENCE = "deny_new_persistence"
    FIELD_REVIEW_REQUIRED = "field_review_required"


@dataclass(frozen=True, slots=True)
class EndpointScope:
    """One exact observed TCP + plaintext WebSocket scope; no lists or dicts."""

    interface: str
    source_ip: str
    captured_source_port: int
    destination_ip: str
    destination_port: int
    listen_port: int
    request_host: str
    request_path: str
    charger_id: str

    def __post_init__(self) -> None:
        if type(self.interface) is not str or _INTERFACE.fullmatch(self.interface) is None:
            raise ValueError("invalid_interface")
        _ipv4(self.source_ip, "invalid_source_ip")
        _port(self.captured_source_port, "invalid_captured_source_port")
        _ipv4(self.destination_ip, "invalid_destination_ip")
        _port(self.destination_port, "invalid_destination_port")
        _port(self.listen_port, "invalid_listen_port")
        if self.destination_port == self.listen_port:
            raise ValueError("redirect_not_required")
        _text(self.request_host, "invalid_websocket_host")
        _text(self.charger_id, "invalid_charger_id")
        path = _text(self.request_path, "invalid_websocket_path")
        if (not path.startswith("/") or path.endswith("/") or "//" in path
                or "?" in path or "#" in path or "%" in path
                or any(part in {".", ".."} for part in path.split("/"))
                or path.rsplit("/", 1)[-1] != self.charger_id):
            raise ValueError("ambiguous_websocket_identity")


@dataclass(frozen=True, slots=True)
class SessionBinding:
    """Claims about one exact post-watermark inbound event and TCP peer.

    This is not a database query or an attestation of physical origin.
    """

    session_id: str
    peer_ip: str
    peer_port: int
    charger_id: str
    event_id: int
    event_session_id: str
    event_charger_id: str
    event_direction: str
    watermark: int
    matching_peer_sessions: int

    def __post_init__(self) -> None:
        for name in ("session_id", "charger_id", "event_session_id", "event_charger_id",
                     "event_direction"):
            _text(getattr(self, name), "invalid_session_binding")
        _ipv4(self.peer_ip, "invalid_session_peer")
        _port(self.peer_port, "invalid_session_peer_port")
        if (type(self.event_id) is not int or self.event_id <= 0
                or type(self.watermark) is not int or self.watermark < 0
                or type(self.matching_peer_sessions) is not int
                or self.matching_peer_sessions < 0):
            raise ValueError("invalid_session_binding")


@dataclass(frozen=True, slots=True)
class ProvenanceEvidence:
    """Declared origin plus an immutable, exact-scope lab inventory."""

    origin: Origin
    endpoint: EndpointScope
    session: SessionBinding | None = None
    synthetic_charger_ids: tuple[str, ...] = ()
    synthetic_destination_ips: tuple[str, ...] = ()
    synthetic_source_ips: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.origin) is not Origin or type(self.endpoint) is not EndpointScope:
            raise ValueError("invalid_provenance_evidence")
        if self.session is not None and type(self.session) is not SessionBinding:
            raise ValueError("invalid_provenance_session")
        _inventory(self.synthetic_charger_ids, ipv4=False)
        _inventory(self.synthetic_destination_ips, ipv4=True)
        _inventory(self.synthetic_source_ips, ipv4=True)


@dataclass(frozen=True, slots=True)
class IsolationAssessment:
    decision: IsolationDecision
    reason: str

    def __post_init__(self) -> None:
        if type(self.decision) is not IsolationDecision:
            raise ValueError("invalid_isolation_decision")
        if type(self.reason) is not str or not self.reason:
            raise ValueError("invalid_isolation_reason")


def assess_isolation(evidence: ProvenanceEvidence) -> IsolationAssessment:
    """Classify only; no outcome authorizes field persistence.

    FIELD is caller-supplied and at most merits independent operator review.
    Missing inventory entries never prove that an endpoint is physical.
    """
    if type(evidence) is not ProvenanceEvidence:
        raise ValueError("invalid_provenance_evidence")
    endpoint = evidence.endpoint
    if evidence.origin is Origin.SYNTHETIC:
        return IsolationAssessment(IsolationDecision.LAB_ONLY, "declared_synthetic_origin")
    if any(IPv4Address(endpoint.destination_ip) in network for network in _LAB_NETWORKS):
        return IsolationAssessment(IsolationDecision.LAB_ONLY, "reserved_lab_destination")
    if endpoint.charger_id in evidence.synthetic_charger_ids:
        return IsolationAssessment(IsolationDecision.LAB_ONLY, "known_synthetic_charger_id")
    if endpoint.destination_ip in evidence.synthetic_destination_ips:
        return IsolationAssessment(IsolationDecision.LAB_ONLY, "known_synthetic_destination")
    if endpoint.source_ip in evidence.synthetic_source_ips:
        return IsolationAssessment(IsolationDecision.LAB_ONLY, "known_synthetic_source")
    if evidence.origin is Origin.UNKNOWN:
        return IsolationAssessment(IsolationDecision.DENY_NEW_PERSISTENCE, "unknown_origin")
    binding = evidence.session
    if binding is None:
        return IsolationAssessment(IsolationDecision.DENY_NEW_PERSISTENCE, "missing_session")
    if (binding.session_id != binding.event_session_id
            or binding.peer_ip != endpoint.source_ip
            or binding.peer_port != endpoint.captured_source_port
            or binding.charger_id != endpoint.charger_id
            or binding.event_charger_id != endpoint.charger_id
            or binding.event_direction != "in"
            or binding.event_id <= binding.watermark
            or binding.matching_peer_sessions != 1):
        return IsolationAssessment(IsolationDecision.DENY_NEW_PERSISTENCE,
                                   "session_scope_or_event_mismatch")
    return IsolationAssessment(IsolationDecision.FIELD_REVIEW_REQUIRED,
                               "consistent_claims_not_physical_attestation")
