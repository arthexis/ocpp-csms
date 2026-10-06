from __future__ import annotations

import argparse

from ocpp_csms.diagnostics import events_between, explain, format_events
from ocpp_csms.status import appliance_status, format_status


DIAGNOSTIC_COMMANDS = ("status", "events", "explain")


def add_diagnostic_commands(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> dict[str, argparse.ArgumentParser]:
    """Register status and diagnostic inspection commands."""
    add = argparse.ArgumentParser.add_argument

    status = subcommands.add_parser("status", help="Show appliance or charger status")
    add(status, "charger", nargs="?", help="Charge point ID")
    add(status, "--charging", action="store_true", help="Show only charging chargers")

    events = subcommands.add_parser("events", help="Show recorded events")
    add(events, "charger", nargs="?", help="Optional charge point ID")
    add(events, "--since", help="ISO-8601 lower timestamp bound")
    add(events, "--until", help="ISO-8601 upper timestamp bound")
    add(events, "--limit", type=int, default=200, help="Maximum events to print")

    explain_parser = subcommands.add_parser("explain", help="Show charger evidence for a time window")
    add(explain_parser, "charger", help="Charge point ID")
    add(explain_parser, "--at", help="ISO-8601 center timestamp")
    add(explain_parser, "--since", help="ISO-8601 lower timestamp bound")
    add(explain_parser, "--until", help="ISO-8601 upper timestamp bound")
    add(explain_parser, "--minutes", type=int, default=10, help="Minutes around --at")

    return {"status": status, "events": events, "explain": explain_parser}


def run_status(args: argparse.Namespace) -> int:
    print(format_status(appliance_status(args.data_dir), charger_id=args.charger, charging_only=args.charging))
    return 0


def run_events(args: argparse.Namespace) -> int:
    if args.limit < 1:
        raise ValueError("--limit must be at least 1")
    rows = events_between(
        args.data_dir,
        charger_id=args.charger,
        since=args.since,
        until=args.until,
        limit=args.limit,
    )
    print(format_events(rows))
    return 0


def run_explain(args: argparse.Namespace) -> int:
    if args.minutes < 0:
        raise ValueError("--minutes must be zero or greater")
    print(
        explain(
            args.data_dir,
            args.charger,
            at=args.at,
            since=args.since,
            until=args.until,
            minutes=args.minutes,
        )
    )
    return 0


def run_diagnostic(args: argparse.Namespace) -> int:
    if args.command == "status":
        return run_status(args)
    if args.command == "events":
        return run_events(args)
    if args.command == "explain":
        return run_explain(args)
    raise ValueError(f"unknown diagnostic command: {args.command}")
