from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ocpp_discover import discover, persistence, redirect
from ocpp_discover.redirect import RedirectReceipt


@dataclass(frozen=True)
class Diagnosis:
    configured_matches: bool
    live_table_present: bool
    observed: RedirectReceipt | None = None

    @property
    def contradiction(self) -> bool:
        return self.observed is not None


def inspect_configuration(
    expected: RedirectReceipt,
    *,
    ruleset_path: str | Path = persistence.DEFAULT_RULESET_PATH,
) -> Diagnosis:
    """Compare durable evidence with owned configured/live state without changing either."""
    path = Path(ruleset_path)
    configured_matches = path.exists() and path.read_text(encoding="utf-8") == persistence.render_persistent_ruleset(expected)
    return Diagnosis(
        configured_matches=configured_matches,
        live_table_present=redirect.table_exists(),
    )


def observe_contradiction(expected: RedirectReceipt, *, seconds: float) -> RedirectReceipt | None:
    """Passively look for the expected charger attempting a different plaintext endpoint."""
    capture = discover.capture_passive_tcp(expected.interface, seconds)
    try:
        observed = discover._websocket_receipt(
            capture,
            interface=expected.interface,
            source_ip=expected.source_ip,
            listen_port=expected.listen_port,
        )
    except ValueError as exc:
        if str(exc) in {"no_plaintext_websocket_upgrade", "secure_or_opaque_traffic"}:
            return None
        raise
    if adaptation_identity(observed) == adaptation_identity(expected):
        return None
    return observed


def adaptation_identity(receipt: RedirectReceipt) -> tuple[object, ...]:
    requests = tuple(sorted((request.destination_ip, request.host, request.path) for request in receipt.requests))
    return (
        receipt.interface,
        receipt.source_ip,
        tuple(sorted(receipt.destination_ips)),
        receipt.destination_port,
        receipt.listen_port,
        requests,
    )
