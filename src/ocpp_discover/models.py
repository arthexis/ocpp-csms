"""Immutable data contracts for discovery and address claims."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from ocpp_discover.redirect import RedirectReceipt


@dataclass(frozen=True)
class DiscoveryCandidate:
    interface: str
    source_mac: str
    source_ip: str
    target_ip: str
    requests: int

    def to_json(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class AddressClaim:
    interface: str
    address: str

    def to_json(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class DiscoveryResult:
    status: str
    charger_id: str | None
    candidate: DiscoveryCandidate | None = None
    redirect: RedirectReceipt | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "status": self.status,
            "charger_id": self.charger_id,
            "candidate": self.candidate.to_json() if self.candidate else None,
            "redirect": self.redirect.to_json() if self.redirect else None,
        }


