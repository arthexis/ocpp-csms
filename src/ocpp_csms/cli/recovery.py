"""Explicit recovery inspection, offline execution, and local policy."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

from ocpp_csms.events import EventStore
from ocpp_csms.recovery import RecoveryPolicy, read_policy, recover_once, write_policy
from ocpp_csms.runtime import process_is_running
from ocpp_csms.schema import inspect_schema, CURRENT_SCHEMA_VERSION
from ocpp_csms.transactions import TransactionArchive


def add_recovery_parser(subcommands):
    parser = subcommands.add_parser("recover", help="Inspect or recover eligible disconnected transactions")
    parser.add_argument("--cp", "--charger", dest="charger")
    parser.add_argument("--dry-run", action="store_true", help="Preview eligible transactions without writing")
    parser.add_argument("--timeout-minutes", type=int, help="Override inactivity timeout for this scan")
    parser.add_argument("--policy", action="store_true", help="Show or configure the persistent recovery policy")
    parser.add_argument("--interval-minutes", type=int, help="Set persistent scan interval (with --policy)")
    parser.add_argument("--enable", action="store_true", help="Enable automatic recovery (with --policy)")
    parser.add_argument("--disable", action="store_true", help="Disable automatic recovery (with --policy)")
    return parser


def run_recovery(args) -> int:
    data = Path(args.data_dir).expanduser()
    policy = read_policy(data)
    if args.enable and args.disable:
        raise ValueError("--enable and --disable are mutually exclusive")
    if args.policy:
        if args.charger or args.dry_run:
            raise ValueError("--policy cannot be combined with recovery selectors")
        timeout = args.timeout_minutes or policy.timeout_seconds // 60
        interval = args.interval_minutes or policy.interval_seconds // 60
        if timeout < 1 or interval < 1:
            raise ValueError("recovery intervals must be positive")
        changed = args.timeout_minutes is not None or args.interval_minutes is not None or args.enable or args.disable
        if changed:
            policy = RecoveryPolicy(
                enabled=False if args.disable else True if args.enable else policy.enabled,
                timeout_seconds=timeout * 60,
                interval_seconds=interval * 60,
            )
            write_policy(data, policy)
        print(asdict(policy))
        return 0
    if args.interval_minutes is not None or args.enable or args.disable:
        raise ValueError("--interval-minutes/--enable/--disable require --policy")
    timeout = (args.timeout_minutes * 60 if args.timeout_minutes is not None
               else policy.timeout_seconds)
    if timeout <= 0:
        raise ValueError("--timeout-minutes must be positive")
    schema = inspect_schema(data)
    if schema.version != CURRENT_SCHEMA_VERSION:
        raise ValueError("recovery requires current database schema; perform explicit schema upgrade first")
    if not args.dry_run and process_is_running(data):
        raise ValueError("stop CSMS before offline recovery; never edit live transaction storage")
    events = EventStore(data)
    archive = TransactionArchive(data)
    server = SimpleNamespace(events=events, transactions=archive, connected_chargers=lambda: [])
    ids = asyncio.run(recover_once(server, timeout_seconds=timeout, charger=args.charger,
                                   dry_run=args.dry_run))
    label = "Eligible" if args.dry_run else "Recovered"
    print(f"{label}: {len(ids)} transaction(s)" + (f" ({', '.join(map(str, ids))})" if ids else ""))
    return 0
