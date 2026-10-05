from __future__ import annotations

from pathlib import Path

from ocpp_discover import discover, persistence, redirect
from ocpp_discover.redirect import RedirectReceipt


def inspect_configuration(
    expected: RedirectReceipt,
    *,
    ruleset_path: str | Path = persistence.DEFAULT_RULESET_PATH,
) -> tuple[bool, bool]:
    """Return whether the owned persistent fragment matches and the live table exists."""
    path = Path(ruleset_path)
    configured_matches = (
        path.exists()
        and path.read_text(encoding="utf-8")
        == persistence.render_persistent_ruleset(expected)
    )
    return configured_matches, redirect.table_exists()


def observe_contradiction(expected: RedirectReceipt, *, seconds: float) -> RedirectReceipt | None:
    """Passively return a different plaintext endpoint attempted by the expected charger."""
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
