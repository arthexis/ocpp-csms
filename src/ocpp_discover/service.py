from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from ocpp_discover import diagnosis, discover, handoff, reconcile
from ocpp_csms.install_cutover import connection_markers, wait_for_reconnect

_DEFAULT_PERSISTENT_DIR = "/var/lib/ocpp-discover"
_DEFAULT_RUNTIME_DIR = "/run/ocpp-discover"
_DEFAULT_BOOT_TIMEOUT = 180.0
_DEFAULT_WAIT_INTERVAL = 5.0
_LOG = logging.getLogger("ocpp-discover")


def _validate(
    data_dir: str | Path,
    chargers: tuple[str, ...],
    *,
    timeout: float,
) -> tuple[str, ...]:
    connections = connection_markers(data_dir, chargers)
    ocpp = handoff._ocpp_markers(data_dir, chargers)
    missing = wait_for_reconnect(data_dir, connections, timeout=timeout)
    if missing:
        return missing
    return handoff.wait_for_fresh_ocpp(data_dir, ocpp, timeout=timeout)


def _wait_for_recovery(
    data_dir: str | Path,
    persistent_dir: str | Path,
    receipt: object,
    *,
    interval: float,
    timeout: float,
) -> dict[str, object]:
    chargers = reconcile.charger_ids(receipt)
    configured, live = diagnosis.inspect_configuration(receipt)
    if not configured:
        _LOG.error("Discover persistent nftables fragment does not match discovered adaptation")
    if not live:
        _LOG.error("Discover live nftables adaptation is absent")

    while True:
        connections = connection_markers(data_dir, chargers)
        ocpp = handoff._ocpp_markers(data_dir, chargers)
        candidate = diagnosis.observe_contradiction(receipt, seconds=interval)

        if not wait_for_reconnect(data_dir, connections, timeout=0):
            if not handoff.wait_for_fresh_ocpp(data_dir, ocpp, timeout=timeout):
                return {"status": "persistent", "chargers": list(chargers)}
            _LOG.error("charger reconnected without fresh inbound OCPP; returning to passive wait")
            continue

        if candidate is None:
            continue

        _LOG.error("positive endpoint contradiction observed; attempting safe reconciliation")
        candidate = reconcile.reconcile(
            data_dir=data_dir,
            persistent_dir=persistent_dir,
            expected=receipt,
            candidate=candidate,
            timeout=timeout,
        )
        return {"status": "reconciled", "chargers": list(reconcile.charger_ids(candidate))}


def _observe_persistent(
    data_dir: str | Path,
    persistent_dir: str | Path,
    *,
    boot_timeout: float,
    wait_interval: float,
    validation_timeout: float,
) -> dict[str, object]:
    receipt = handoff.load_discovered(persistent_dir)
    chargers = reconcile.charger_ids(receipt)
    missing = _validate(data_dir, chargers, timeout=boot_timeout)
    if not missing:
        return {"status": "persistent", "chargers": list(chargers)}

    _LOG.error(
        "expected charger(s) not observed during startup window: %s; persistent adaptation unchanged; entering passive offline wait",
        ", ".join(missing),
    )
    return _wait_for_recovery(
        data_dir,
        persistent_dir,
        receipt,
        interval=wait_interval,
        timeout=validation_timeout,
    )


def run_service(
    *,
    data_dir: str | Path,
    runtime_dir: str | Path = _DEFAULT_RUNTIME_DIR,
    persistent_dir: str | Path = _DEFAULT_PERSISTENT_DIR,
    interface: str = "eth0",
    listen_port: int = 9000,
    grace_seconds: float = 10.0,
    arp_seconds: float = 15.0,
    tcp_seconds: float = 15.0,
    connect_timeout: float = 30.0,
    boot_timeout: float = _DEFAULT_BOOT_TIMEOUT,
    wait_interval: float = _DEFAULT_WAIT_INTERVAL,
):
    """Observe known state; reconcile contradictions; discover only when state is absent."""
    if handoff.discovered_path(persistent_dir).exists():
        return _observe_persistent(
            data_dir,
            persistent_dir,
            boot_timeout=boot_timeout,
            wait_interval=wait_interval,
            validation_timeout=connect_timeout,
        )

    result = discover.run_discovery(
        data_dir=data_dir,
        state_dir=runtime_dir,
        interface=interface,
        listen_port=listen_port,
        grace_seconds=grace_seconds,
        arp_seconds=arp_seconds,
        tcp_seconds=tcp_seconds,
        connect_timeout=connect_timeout,
    )
    return {"status": "discovered", "result": result.to_json()}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ocpp_discover service",
        description="Observe, diagnose, and safely reconcile a persistent OCPP adaptation.",
    )
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
    parser.add_argument("--wait-interval", type=float, default=_DEFAULT_WAIT_INTERVAL)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        outcome = run_service(
            data_dir=args.data_dir,
            runtime_dir=args.runtime_dir,
            persistent_dir=args.persistent_dir,
            interface=args.interface,
            listen_port=args.listen_port,
            grace_seconds=args.grace_seconds,
            arp_seconds=args.arp_seconds,
            tcp_seconds=args.tcp_seconds,
            connect_timeout=args.connect_timeout,
            boot_timeout=args.boot_timeout,
            wait_interval=args.wait_interval,
        )
    except (RuntimeError, ValueError, OSError) as exc:
        _LOG.error("%s", exc)
        return 1
    print(json.dumps(outcome, indent=2, sort_keys=True))
    return 0
