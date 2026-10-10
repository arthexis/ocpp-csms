"""Argument parser for the OCPP Discover command-line interface."""

from __future__ import annotations

import argparse

_DEFAULT_INTERFACE = "eth0"
_DEFAULT_SECONDS = 15.0
_MIN_REQUESTS = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m field.discover", description="Discover and bootstrap a charger-facing Ethernet endpoint.")
    subparsers = parser.add_subparsers(dest="command")
    run_parser = subparsers.add_parser("run", help="Run transactional discovery and preserve successful network state")
    run_parser.add_argument("--data-dir", required=True)
    run_parser.add_argument("--state-dir", required=True)
    run_parser.add_argument("--interface", default=_DEFAULT_INTERFACE)
    run_parser.add_argument("--listen-port", type=int, default=9000)
    run_parser.add_argument("--grace-seconds", type=float, default=10.0)
    run_parser.add_argument("--arp-seconds", type=float, default=_DEFAULT_SECONDS)
    run_parser.add_argument("--tcp-seconds", type=float, default=_DEFAULT_SECONDS)
    run_parser.add_argument("--connect-timeout", type=float, default=30.0)
    run_parser.add_argument(
        "--existing-endpoint-only",
        action="store_true",
        help="Capture only an existing host-local OCPP endpoint; never fall back to ARP/address claiming.",
    )
    run_parser.add_argument(
        "--passive-diagnostic-only",
        action="store_true",
        help="Record and report an existing host-local endpoint without installing a redirect.",
    )
    run_parser.add_argument(
        "--force-passive-capture",
        action="store_true",
        help="Bypass the current-session grace check and begin passive capture immediately.",
    )
    run_parser.add_argument(
        "--passive-capture-log",
        help="Write the bounded passive TCP capture to a new local file for diagnosis.",
    )
    cleanup_parser = subparsers.add_parser("cleanup", help="Remove discovery-owned redirect and address state")
    cleanup_parser.add_argument("--state-dir", required=True)
    parser.add_argument("--interface", default=_DEFAULT_INTERFACE)
    parser.add_argument("--seconds", type=float, default=_DEFAULT_SECONDS)
    parser.add_argument("--min-requests", type=int, default=_MIN_REQUESTS)
    return parser


