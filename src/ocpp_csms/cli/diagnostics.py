from __future__ import annotations

import argparse

from ocpp_csms.diagnostics import events_between, explain, format_events
from ocpp_csms.event_contract import events_contract, raw_events_contract
from ocpp_csms.output import emit_json
from ocpp_csms.status import appliance_status, format_status
from ocpp_csms.status_contract import status_contract

DIAGNOSTIC_COMMANDS = ("status", "events", "explain")


def add_diagnostic_commands(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> dict[str, argparse.ArgumentParser]:
    add = argparse.ArgumentParser.add_argument
    status = subcommands.add_parser("status", help="Show appliance or charger status")
    add(status, "charger", nargs="?", help="Charge point ID")
    add(status, "--charging", action="store_true", help="Show only charging chargers")
    add(status, "-j", "--json", action="store_true", help="Print the stable machine-readable status contract")

    events = subcommands.add_parser("events", help="Show recorded events")
    add(events, "charger", nargs="?", help="Optional charge point ID")
    add(events, "--transaction", "--txn", dest="transaction", type=int, help="Filter by OCPP transaction ID")
    add(events, "--since", help="ISO-8601 lower timestamp bound")
    add(events, "--until", help="ISO-8601 upper timestamp bound")
    add(events, "--limit", type=int, default=200, help="Maximum events to print")
    add(events, "-j", "--json", action="store_true", help="Print the stable machine-readable event contract")
    add(events, "--raw", action="store_true", help="Include raw diagnostic payloads (requires --json)")
    add(events, "--verbose", action="store_true", help="Show every original event with full payload, without grouping")

    explain_parser = subcommands.add_parser("explain", help="Show charger evidence for a time window")
    add(explain_parser, "charger", help="Charge point ID")
    add(explain_parser, "--at", help="ISO-8601 center timestamp")
    add(explain_parser, "--since", help="ISO-8601 lower timestamp bound")
    add(explain_parser, "--until", help="ISO-8601 upper timestamp bound")
    add(explain_parser, "--minutes", type=int, default=10, help="Minutes around --at")
    return {"status": status, "events": events, "explain": explain_parser}


def run_status(args: argparse.Namespace) -> int:
    status = appliance_status(args.data_dir)
    if args.json:
        emit_json(status_contract(status, charger_id=args.charger, charging_only=args.charging))
    else:
        print(format_status(status, charger_id=args.charger, charging_only=args.charging))
    return 0


def run_events(args: argparse.Namespace) -> int:
    if args.limit < 1:
        raise ValueError("--limit must be at least 1")
    if args.transaction is not None and args.transaction < 0:
        raise ValueError("--transaction/--txn must be zero or greater")
    if args.raw and not args.json:
        raise ValueError("--raw requires --json")
    if args.verbose and args.json:
        raise ValueError("--verbose cannot be combined with --json")
    rows = events_between(args.data_dir, charger_id=args.charger, transaction_id=args.transaction, since=args.since, until=args.until, limit=args.limit)
    if args.json:
        emit_json(raw_events_contract(rows) if args.raw else events_contract(rows))
    else:
        print(format_events(rows, verbose=args.verbose))
    return 0


def run_explain(args: argparse.Namespace) -> int:
    print(explain(args.data_dir, args.charger, at=args.at, since=args.since, until=args.until, minutes=args.minutes))
    return 0


def run_diagnostic(args: argparse.Namespace) -> int:
    if args.command == "status": return run_status(args)
    if args.command == "events": return run_events(args)
    if args.command == "explain": return run_explain(args)
    raise ValueError(f"unknown diagnostic command: {args.command}")
