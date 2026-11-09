from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import signal
import sqlite3
from pathlib import Path
import threading
import time
from typing import Any

from ocpp_discover import discover as core
from ocpp_discover import provisional, handoff
from ocpp_csms.schema import DATABASE_FILENAME
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


def _event_watermark(data_dir: str | Path) -> int:
    """Snapshot the global inbound event cursor before opening a redirect."""
    database = Path(data_dir).expanduser() / DATABASE_FILENAME
    if not database.exists():
        return 0
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        row = connection.execute("SELECT COALESCE(MAX(id), 0) FROM events WHERE direction = 'in'").fetchone()
        return int(row[0]) if row else 0


def _captured_source_ports(capture: str, source_ip: str, charger_id: str) -> set[int]:
    """Only ports carrying a matching plaintext WebSocket upgrade qualify."""
    from ocpp_discover.websocket import _tcp_blocks, _GET, _UPGRADE, _CONNECTION
    ports: set[int] = set()
    for packet, payload in _tcp_blocks(capture):
        if packet.group("src") != source_ip:
            continue
        get = _GET.search(payload)
        connection = _CONNECTION.search(payload)
        if not (get and _UPGRADE.search(payload) and connection):
            continue
        if "upgrade" not in {part.strip().lower() for part in connection.group("value").split(",")}:
            continue
        if get.group("path").split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1] == charger_id:
            ports.add(int(packet.group("src_port")))
    return ports


def _session_proven(data_dir: str | Path, charger_id: str, source_ip: str,
                    source_ports: set[int], watermark: int) -> bool:
    """Require inbound evidence linked to the exact observed TCP peer session."""
    if not source_ports:
        return False
    database = Path(data_dir).expanduser() / DATABASE_FILENAME
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        try:
            # A reused TCP peer tuple is ambiguous without TCP sequence/epoch proof.
            # Fail closed rather than attributing a later connection to the capture.
            duplicates = connection.execute(
                """SELECT COUNT(*) FROM discovery_sessions
                   WHERE charger_id = ? AND remote_ip = ? AND remote_port IN (%s)""" % ",".join("?" for _ in source_ports),
                (charger_id, source_ip, *sorted(source_ports)),
            ).fetchone()[0]
            if duplicates != 1:
                return False
            row = connection.execute(
                """SELECT 1 FROM events AS e
                   JOIN discovery_event_sessions AS x ON x.event_id = e.id
                   JOIN discovery_sessions AS s ON s.session_id = x.session_id
                   WHERE e.id > ? AND e.direction = 'in'
                     AND e.charger_id = ? AND s.charger_id = ?
                     AND s.remote_ip = ? AND s.remote_port IN (%s)
                   LIMIT 1""" % ",".join("?" for _ in source_ports),
                (watermark, charger_id, charger_id, source_ip, *sorted(source_ports)),
            ).fetchone()
        except sqlite3.OperationalError:
            return False  # Legacy databases cannot prove session identity.
    return row is not None


def _run_local_syn_discovery(*, initial_evidence: str, candidate: provisional.LocalSynCandidate, **kwargs):
    """Redirect one bounded SYN scope and require matching WebSocket and CSMS proof."""
    data_dir = kwargs["data_dir"]
    tcp_seconds = float(kwargs.get("tcp_seconds", 15.0))
    connect_timeout = float(kwargs.get("connect_timeout", 30.0))
    poll_interval = float(kwargs.get("poll_interval", 0.5))
    event_watermark = _event_watermark(data_dir)

    # Turn the normal systemd stop signal into a Python exception so the
    # temporary table is removed before the service exits.
    previous_sigterm = None
    if threading.current_thread() is threading.main_thread():
        previous_sigterm = signal.getsignal(signal.SIGTERM)
        def terminate(signum, frame):
            raise SystemExit(128 + signum)
        signal.signal(signal.SIGTERM, terminate)
    applied = False
    try:
        provisional.apply(candidate)
        applied = True
        # The SYN that justified the provisional rule was already rejected by the host.
        # Keep capture open for one configured reconnect window plus the ordinary TCP
        # observation window so the charger's next retry can expose its WebSocket identity.
        proof_seconds = max(tcp_seconds, connect_timeout + tcp_seconds)
        capture = core.capture_passive_tcp(candidate.interface, proof_seconds)
        try:
            receipt = core._websocket_receipt(
                capture, interface=candidate.interface, listen_port=candidate.listen_port,
                source_ip=candidate.source_ip,
                destination_filter={candidate.destination_ip},
            )
        except ValueError as exc:
            if str(exc) in {"no_plaintext_websocket_upgrade", "secure_or_opaque_traffic"}:
                receipt = None
            else:
                raise
        if receipt is None:
            raise RuntimeError("provisional_redirect_unproven")
        if not provisional.matches(candidate, receipt):
            raise RuntimeError("provisional_redirect_evidence_mismatch")

        # Never select the first connected charger: an unrelated existing
        # connection must not hide the identity actually observed on the wire.
        expected_ids = {request.path.rstrip("/").rsplit("/", 1)[-1] for request in receipt.requests}
        if len(expected_ids) != 1 or not all(expected_ids):
            raise RuntimeError("ambiguous_charger_identity")
        charger_id = next(iter(expected_ids))
        deadline = time.monotonic() + connect_timeout
        while charger_id not in core.connected_chargers(data_dir):
            if __import__("time").monotonic() >= deadline:
                raise RuntimeError("charger_connection_timeout")
            __import__("time").sleep(min(poll_interval, max(0.0, deadline - __import__("time").monotonic())))
        source_ports = _captured_source_ports(capture, candidate.source_ip, charger_id)
        deadline = time.monotonic() + connect_timeout
        while not _session_proven(data_dir, charger_id, candidate.source_ip, source_ports, event_watermark):
            if time.monotonic() >= deadline:
                raise RuntimeError("websocket_session_unproven")
            time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))

        result = core.DiscoveryResult("connected", charger_id, None, receipt)
        return ProvenDiscoveryResult(charger_id, receipt, result)
    except BaseException:
        if applied:
            provisional.remove()
        raise
    finally:
        if previous_sigterm is not None:
            signal.signal(signal.SIGTERM, previous_sigterm)


def run_discovery(*, initial_evidence: str, **kwargs):
    """Run normal discovery, with a narrowly scoped plaintext-SYN path when available.

    A fresh SYN to a supported plaintext non-listener port may justify one temporary exact-match
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
