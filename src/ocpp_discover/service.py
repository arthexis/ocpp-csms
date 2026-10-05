from __future__ import annotations

import argparse
import json
from pathlib import Path

from ocpp_discover import discover, handoff, restore

_DEFAULT_PERSISTENT_DIR = "/var/lib/ocpp-csms/discover"
_DEFAULT_RUNTIME_DIR = "/run/ocpp-discover"


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
):
    """Restore durable adaptation when present; otherwise run fresh discovery."""
    persistent_path = handoff.persistent_receipt_path(persistent_dir)
    if persistent_path.exists():
        ruleset = restore.restore_path_a(
            persistent_dir=persistent_dir,
            runtime_dir=runtime_dir,
            listen_port=listen_port,
        )
        return {"status": "restored", "ruleset": ruleset}

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
        description="Restore validated OCPP network adaptation or discover one when none exists.",
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
        )
    except (RuntimeError, ValueError, OSError) as exc:
        print(str(exc))
        return 1
    print(json.dumps(outcome, indent=2, sort_keys=True))
    return 0
