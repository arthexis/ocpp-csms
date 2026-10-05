from __future__ import annotations

from collections.abc import Callable

from ocpp_discover import discover as core


def run_discovery(*, initial_evidence: str, **kwargs):
    """Run normal discovery with the wake capture prepended to its first TCP capture.

    Discovery remains responsible for validation and mutation. This adapter only
    ensures that evidence observed before the bounded discovery phase is not
    discarded at the phase boundary.
    """
    if not initial_evidence:
        raise ValueError("initial_evidence_required")

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
        return core.run_discovery(**kwargs)
    finally:
        core.capture_passive_tcp = capture_passive_tcp
