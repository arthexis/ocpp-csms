from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ocpp_discover import discover as core
from ocpp_discover import provisional
from ocpp_discover.redirect import RedirectReceipt


@dataclass(frozen=True)
class ProvenDiscoveryResult:
    charger_id: str | None
    receipt: RedirectReceipt
    original: Any

    def to_json(self) -> dict[str, object]:
        return self.original.to_json()


def _with_receipt(result: Any) -> Any:
    receipt = getattr(result, "redirect", None)
    if receipt is None or hasattr(result, "receipt"):
        return result
    return ProvenDiscoveryResult(getattr(result, "charger_id", None), receipt, result)


def _run_local_syn_discovery(*, initial_evidence: str, candidate: provisional.LocalSynCandidate, **kwargs):
    """Temporarily redirect one proven-local SYN scope, then require WebSocket + CSMS proof."""
    data_dir = kwargs["data_dir"]
    tcp_seconds = float(kwargs.get("tcp_seconds", 15.0))
    connect_timeout = float(kwargs.get("connect_timeout", 30.0))
    poll_interval = float(kwargs.get("poll_interval", 0.5))

    provisional.apply(candidate)
    try:
        # The SYN that justified the provisional rule was already rejected by the host.
        # Keep capture open for one configured reconnect window plus the ordinary TCP
        # observation window so the charger's next retry can expose its WebSocket identity.
        proof_seconds = max(tcp_seconds, connect_timeout + tcp_seconds)
        receipt = core.discover_existing_endpoint(
            interface=candidate.interface,
            listen_port=candidate.listen_port,
            seconds=proof_seconds,
        )
        if receipt is None:
            raise RuntimeError("provisional_redirect_unproven")
        if not provisional.matches(candidate, receipt):
            raise RuntimeError("provisional_redirect_evidence_mismatch")

        charger_id = core.wait_for_charger(data_dir, connect_timeout, poll_interval)
        if not charger_id:
            raise RuntimeError("charger_connection_timeout")

        result = core.DiscoveryResult("connected", charger_id, None, receipt)
        return ProvenDiscoveryResult(charger_id, receipt, result)
    except Exception:
        provisional.remove()
        raise


def run_discovery(*, initial_evidence: str, **kwargs):
    """Run normal discovery, with a narrowly scoped local-SYN path when available.

    A fresh SYN to a host-local non-listener port may justify one temporary exact-match
    redirect. It never becomes persistent evidence by itself: the redirected retry must
    expose a plaintext WebSocket identity and reach the CSMS before a receipt is returned.
    Otherwise the temporary table is removed and the attempt fails.
    """
    if not initial_evidence:
        raise ValueError("initial_evidence_required")

    interface = str(kwargs.get("interface", "eth0"))
    listen_port = int(kwargs.get("listen_port", 9000))
    candidate = provisional.local_syn_candidate(
        initial_evidence,
        interface=interface,
        listen_port=listen_port,
    )
    if candidate is not None:
        return _run_local_syn_discovery(
            initial_evidence=initial_evidence,
            candidate=candidate,
            **kwargs,
        )

    capture_passive_tcp: Callable[[str, float], str] = core.capture_passive_tcp
    used = False

    def seeded_capture(interface: str, seconds: float) -> str:
        nonlocal used
        capture = capture_passive_tcp(interface, seconds)
        if used:
            return capture
        used = True
        return initial_evidence + capture

    # run_discovery is synchronous and the service runs one discovery attempt at
    # a time. Restore the module function even when discovery fails.
    core.capture_passive_tcp = seeded_capture
    try:
        return _with_receipt(core.run_discovery(**kwargs))
    finally:
        core.capture_passive_tcp = capture_passive_tcp
