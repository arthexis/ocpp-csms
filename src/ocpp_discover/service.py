from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from ocpp_discover import diagnosis, first_contact as discover, handoff, reconcile
from ocpp_csms.install_cutover import connection_markers, wait_for_reconnect

_DEFAULT_PERSISTENT_DIR = "/var/lib/ocpp-discover"
_DEFAULT_RUNTIME_DIR = "/run/ocpp-discover"
_DEFAULT_BOOT_TIMEOUT = 180.0
_DEFAULT_DIAGNOSIS_SECONDS = 5.0
_LOG = logging.getLogger("ocpp-discover")


def _validate(data_dir: str | Path, chargers: tuple[str, ...], *, timeout: float) -> tuple[str, ...]:
    connections = connection_markers(data_dir, chargers)
    ocpp = handoff._ocpp_markers(data_dir, chargers)
    missing = wait_for_reconnect(data_dir, connections, timeout=timeout)
    return missing or handoff.wait_for_fresh_ocpp(data_dir, ocpp, timeout=timeout)


def _wait_for_recovery(data_dir: str | Path, persistent_dir: str | Path, receipt: object, *, diagnosis_seconds: float, timeout: float) -> dict[str, object]:
    chargers = reconcile.charger_ids(receipt)
    configuration = diagnosis.inspect_configuration(receipt)
    if not configuration.configured_matches:
        _LOG.error("Discover persistent nftables fragment does not match discovered adaptation")
    if not configuration.live_table_present:
        _LOG.error("Discover live nftables adaptation is absent")
    while True:
        evidence = diagnosis.wait_for_discovery_evidence(receipt.interface, expected=receipt)
        connections = connection_markers(data_dir, chargers)
        ocpp = handoff._ocpp_markers(data_dir, chargers)
        if not wait_for_reconnect(data_dir, connections, timeout=0):
            if not handoff.wait_for_fresh_ocpp(data_dir, ocpp, timeout=timeout):
                return {"status": "persistent", "chargers": list(chargers)}
            _LOG.error("charger reconnected without fresh inbound OCPP; returning to passive wait")
            continue
        candidate = diagnosis.observe_endpoint(receipt.interface, listen_port=receipt.listen_port, seconds=diagnosis_seconds)
        if candidate is None or diagnosis.adaptation_identity(candidate) == diagnosis.adaptation_identity(receipt):
            continue
        _LOG.error("positive endpoint contradiction observed after %s activity; attempting safe reconciliation", evidence.kind)
        candidate = reconcile.reconcile(data_dir=data_dir, persistent_dir=persistent_dir, expected=receipt, candidate=candidate, timeout=timeout)
        return {"status": "reconciled", "chargers": list(reconcile.charger_ids(candidate))}


def _observe_persistent(data_dir: str | Path, persistent_dir: str | Path, *, boot_timeout: float, diagnosis_seconds: float, validation_timeout: float) -> dict[str, object]:
    receipt = handoff.load_discovered(persistent_dir)
    chargers = reconcile.charger_ids(receipt)
    missing = _validate(data_dir, chargers, timeout=boot_timeout)
    if not missing:
        return {"status": "persistent", "chargers": list(chargers)}
    _LOG.error("expected charger(s) not observed during startup window: %s; persistent adaptation unchanged; entering passive offline wait", ", ".join(missing))
    return _wait_for_recovery(data_dir, persistent_dir, receipt, diagnosis_seconds=diagnosis_seconds, timeout=validation_timeout)


def _wait_then_discover(*, data_dir: str | Path, runtime_dir: str | Path, persistent_dir: str | Path, interface: str, listen_port: int, grace_seconds: float, arp_seconds: float, tcp_seconds: float, connect_timeout: float) -> dict[str, object]:
    """Wait silently for first charger evidence, prove it, and promote it to durable state."""
    evidence = diagnosis.wait_for_discovery_evidence(interface)
    result = discover.run_discovery(
        data_dir=data_dir,
        state_dir=runtime_dir,
        interface=interface,
        listen_port=listen_port,
        grace_seconds=grace_seconds,
        arp_seconds=arp_seconds,
        tcp_seconds=tcp_seconds,
        connect_timeout=connect_timeout,
        initial_evidence=evidence.capture,
    )
    receipt = getattr(result, "receipt", None)
    charger_id = getattr(result, "charger_id", None)
    if receipt is None or charger_id is None:
        raise RuntimeError("discovery_did_not_produce_proven_adaptation")
    handoff._promote_persistent_adaptation(persistent_dir, receipt)
    return {"status": "discovered", "chargers": [charger_id], "result": result.to_json()}


def run_service(*, data_dir: str | Path, runtime_dir: str | Path = _DEFAULT_RUNTIME_DIR, persistent_dir: str | Path = _DEFAULT_PERSISTENT_DIR, interface: str = "eth0", listen_port: int = 9000, grace_seconds: float = 10.0, arp_seconds: float = 15.0, tcp_seconds: float = 15.0, connect_timeout: float = 30.0, boot_timeout: float = _DEFAULT_BOOT_TIMEOUT, wait_interval: float = _DEFAULT_DIAGNOSIS_SECONDS, max_cycles: int | None = None):
    """Continuously observe, diagnose, and reconcile the proven charger adaptation.

    ``max_cycles`` exists only to make bounded unit tests possible. Production callers
    leave it unset, so successful discovery/validation never terminates the service.
    """
    cycles = 0

    if handoff.discovered_path(persistent_dir).exists():
        outcome = _observe_persistent(data_dir, persistent_dir, boot_timeout=boot_timeout, diagnosis_seconds=wait_interval, validation_timeout=connect_timeout)
    else:
        outcome = _wait_then_discover(data_dir=data_dir, runtime_dir=runtime_dir, persistent_dir=persistent_dir, interface=interface, listen_port=listen_port, grace_seconds=grace_seconds, arp_seconds=arp_seconds, tcp_seconds=tcp_seconds, connect_timeout=connect_timeout)

    cycles += 1
    if max_cycles is not None and cycles >= max_cycles:
        return outcome

    while True:
        receipt = handoff.load_discovered(persistent_dir)
        outcome = _wait_for_recovery(data_dir, persistent_dir, receipt, diagnosis_seconds=wait_interval, timeout=connect_timeout)
        cycles += 1
        if max_cycles is not None and cycles >= max_cycles:
            return outcome


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ocpp_discover service", description="Continuously observe, diagnose, and safely reconcile a persistent OCPP adaptation.")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--runtime-dir", default=_DEFAULT_RUNTIME_DIR)
    parser.add_argument("--persistent-dir", default=_DEFAULT_PERSISTENT_DIR)
    parser.add_argument("--interface", default="eth0")
    parser.add_argument("--listen-port", type=int, default=9000)
    parser.add_argument("--grace-seconds", type=float, default=10.0)
    parser.add_argument("--arp-seconds", type=float, default=15.0)
    parser.add_argument("--tcp-seconds", type=float, default=15.0)
    parser.add_argument("--connect-timeout", type=float, default=30.0)
    parser.add_argument("--boot-timeout", type=float, default=_DEFAULT_BOOT_TIMEOUT)
    parser.add_argument("--wait-interval", type=float, default=_DEFAULT_DIAGNOSIS_SECONDS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        outcome = run_service(data_dir=args.data_dir, runtime_dir=args.runtime_dir, persistent_dir=args.persistent_dir, interface=args.interface, listen_port=args.listen_port, grace_seconds=args.grace_seconds, arp_seconds=args.arp_seconds, tcp_seconds=args.tcp_seconds, connect_timeout=args.connect_timeout, boot_timeout=args.boot_timeout, wait_interval=args.wait_interval)
    except (RuntimeError, ValueError, OSError) as exc:
        _LOG.error("%s", exc)
        return 1
    print(json.dumps(outcome, indent=2, sort_keys=True))
    return 0
