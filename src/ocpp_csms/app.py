from __future__ import annotations

import argparse
import asyncio
import logging

from ocpp_csms.diagnostics import events_between, explain, explain_between, format_events
from ocpp_csms.events import EventStore
from ocpp_csms.server import CSMSServer
from ocpp_csms.status import appliance_status, format_status
from ocpp_csms.transactions import TransactionArchive, default_data_dir


def build_parser() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    parser = argparse.ArgumentParser(
        prog="ocpp-csms",
        description="Small OCPP 1.6J CSMS appliance.",
    )
    parser.add_argument(
        "--data-dir",
        default=str(default_data_dir()),
        help="Writable data directory (default: %(default)s)",
    )
    subcommands = parser.add_subparsers(dest="command")

    serve = subcommands.add_parser("serve", help="Run the OCPP server")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=9000)
    serve.add_argument("--log-level", default="INFO")

    status = subcommands.add_parser("status", help="Show appliance or charger status")
    status.add_argument("charger", nargs="?", help="Charge point ID")
    status.add_argument(
        "--charging",
        action="store_true",
        help="Show only chargers that appear to be charging",
    )

    events = subcommands.add_parser("events", help="Show recorded OCPP and runtime events")
    events.add_argument("charger", nargs="?", help="Optional charge point ID")
    events.add_argument("--since", help="ISO-8601 lower timestamp bound")
    events.add_argument("--until", help="ISO-8601 upper timestamp bound")
    events.add_argument("--limit", type=int, default=200, help="Maximum events to print")

    explain_parser = subcommands.add_parser(
        "explain",
        help="Show evidence around a charger and time or between two times",
    )
    explain_parser.add_argument("charger", help="Charge point ID")
    explain_parser.add_argument("--at", help="ISO-8601 center timestamp")
    explain_parser.add_argument("--since", help="ISO-8601 lower timestamp bound")
    explain_parser.add_argument("--until", help="ISO-8601 upper timestamp bound")
    explain_parser.add_argument(
        "--minutes",
        type=int,
        default=10,
        help="Minutes before and after --at to inspect (default: %(default)s)",
    )

    help_parser = subcommands.add_parser("help", help="Show commands and parameters")
    help_parser.add_argument(
        "topic",
        nargs="?",
        choices=("serve", "status", "events", "explain"),
    )
    return parser, {
        "serve": serve,
        "status": status,
        "events": events,
        "explain": explain_parser,
    }


def print_help(
    parser: argparse.ArgumentParser,
    commands: dict[str, argparse.ArgumentParser],
    topic: str | None = None,
) -> None:
    if topic is not None:
        commands[topic].print_help()
        return
    parser.print_help()
    for command in commands.values():
        print()
        command.print_help()


async def run_server(args: argparse.Namespace) -> None:
    logging.basicConfig(level=args.log_level.upper())
    transactions = TransactionArchive(args.data_dir)
    events = EventStore(args.data_dir)
    server = CSMSServer(
        host=args.host,
        port=args.port,
        transactions=transactions,
        events=events,
    )
    await server.serve_forever()


def run_status(args: argparse.Namespace) -> int:
    data = appliance_status(args.data_dir)
    print(
        format_status(
            data,
            charger_id=args.charger,
            charging_only=args.charging,
        )
    )
    return 0


def run_events(args: argparse.Namespace) -> int:
    if args.limit < 1:
        raise SystemExit("--limit must be at least 1")
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
    if args.at:
        if args.since or args.until:
            raise SystemExit("use either --at or --since/--until, not both")
        if args.minutes < 1:
            raise SystemExit("--minutes must be at least 1")
        print(explain(args.data_dir, args.charger, args.at, minutes=args.minutes))
        return 0

    if bool(args.since) != bool(args.until):
        raise SystemExit("--since and --until must be supplied together for explain")
    if args.since and args.until:
        print(explain_between(args.data_dir, args.charger, args.since, args.until))
        return 0

    raise SystemExit("explain requires --at TIME or --since TIME --until TIME")


def main() -> int:
    parser, commands = build_parser()
    args = parser.parse_args()

    if args.command is None:
        print_help(parser, commands)
        return 0
    if args.command == "help":
        print_help(parser, commands, args.topic)
        return 0
    if args.command == "status":
        return run_status(args)
    if args.command == "events":
        return run_events(args)
    if args.command == "explain":
        return run_explain(args)
    if args.command == "serve":
        asyncio.run(run_server(args))
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
