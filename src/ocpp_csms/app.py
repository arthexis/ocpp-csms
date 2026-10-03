from __future__ import annotations

import argparse
import asyncio
import logging

from ocpp_csms.control import send_control
from ocpp_csms.diagnostics import events_between, explain, format_events
from ocpp_csms.events import EventStore
from ocpp_csms.server import CSMSServer
from ocpp_csms.status import appliance_status, format_status
from ocpp_csms.transaction_cli import format_transaction, format_transactions
from ocpp_csms.transaction_query import TransactionQuery
from ocpp_csms.transactions import TransactionArchive, default_data_dir


CONTROL_COMMANDS = ("start", "stop", "reboot")


def build_parser() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    parser = argparse.ArgumentParser(prog="ocpp-csms", description="Small OCPP 1.6J CSMS appliance.")
    add = argparse.ArgumentParser.add_argument
    add(parser, "--data-dir", default=str(default_data_dir()), help="Writable data directory (default: %(default)s)")
    subcommands = parser.add_subparsers(dest="command")

    init = subcommands.add_parser("init", help="Initialize appliance storage")

    serve = subcommands.add_parser("serve", help="Run the OCPP server")
    add(serve, "--host", default="0.0.0.0")
    add(serve, "--port", type=int, default=9000)
    add(serve, "--log-level", default="INFO")

    start = subcommands.add_parser("start", help="Request remote transaction start")
    add(start, "charger", help="Charge point ID")
    add(start, "--connector", type=int, help="Connector ID")
    add(start, "--id-tag", required=True, help="OCPP idTag for the remote start")

    stop = subcommands.add_parser("stop", help="Request remote transaction stop")
    add(stop, "charger", help="Charge point ID")
    add(stop, "--transaction", type=int, required=True, help="OCPP transaction ID")

    reboot = subcommands.add_parser("reboot", help="Request charger reset")
    add(reboot, "charger", help="Charge point ID")
    add(reboot, "--hard", action="store_true", help="Request a Hard reset instead of Soft")

    status = subcommands.add_parser("status", help="Show appliance or charger status")
    add(status, "charger", nargs="?", help="Charge point ID")
    add(status, "--charging", action="store_true", help="Show only charging chargers")

    transactions = subcommands.add_parser(
        "transactions",
        aliases=["txn"],
        help="Inspect archived transactions",
    )
    transactions.set_defaults(command="transactions")
    add(transactions, "transaction_id", nargs="?", type=int, help="Transaction ID for detailed inspection")
    selection = transactions.add_mutually_exclusive_group()
    selection.add_argument("--active", action="store_true", help="Show only active transactions")
    selection.add_argument("--last", action="store_true", help="Show the most recent non-active transaction")
    add(transactions, "--charger", help="Filter by charge point ID")
    add(transactions, "--connector", "--cp", dest="connector", type=int, help="Filter by connector ID")
    add(transactions, "--id-tag", help="Filter by OCPP idTag")
    add(transactions, "--since", help="ISO-8601 lower timestamp bound")
    add(transactions, "--until", help="ISO-8601 upper timestamp bound")
    add(transactions, "--limit", type=int, default=20, help="Maximum transactions to print (default: %(default)s)")

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

    help_parser = subcommands.add_parser("help", help="Show commands and parameters")
    topics = ("init", "serve", "start", "stop", "reboot", "status", "transactions", "txn", "events", "explain")
    add(help_parser, "topic", nargs="?", choices=topics)
    commands = {
        "init": init,
        "serve": serve,
        "start": start,
        "stop": stop,
        "reboot": reboot,
        "status": status,
        "transactions": transactions,
        "txn": transactions,
        "events": events,
        "explain": explain_parser,
    }
    return parser, commands


def print_help(parser: argparse.ArgumentParser, commands: dict[str, argparse.ArgumentParser], topic: str | None = None) -> None:
    if topic:
        commands[topic].print_help()
        return
    parser.print_help()
    printed: set[int] = set()
    for command in commands.values():
        identity = id(command)
        if identity in printed:
            continue
        printed.add(identity)
        print()
        command.print_help()


async def run_server(args: argparse.Namespace) -> None:
    logging.basicConfig(level=args.log_level.upper())
    await CSMSServer(
        host=args.host,
        port=args.port,
        transactions=TransactionArchive(args.data_dir),
        events=EventStore(args.data_dir),
    ).serve_forever()


def initialize_storage(data_dir: str) -> None:
    TransactionArchive(data_dir)
    EventStore(data_dir)


def control_request(args: argparse.Namespace) -> dict[str, object]:
    request: dict[str, object] = {"command": args.command, "charger": args.charger}
    if args.command == "start":
        request["id_tag"] = args.id_tag
        if args.connector is not None:
            request["connector"] = args.connector
    elif args.command == "stop":
        request["transaction"] = args.transaction
    elif args.command == "reboot":
        request["type"] = "Hard" if args.hard else "Soft"
    return request


def run_control(args: argparse.Namespace) -> int:
    if args.command == "start" and args.connector is not None and args.connector < 0:
        raise ValueError("--connector must be zero or greater")
    if args.command == "stop" and args.transaction < 0:
        raise ValueError("--transaction must be zero or greater")

    try:
        response = asyncio.run(send_control(args.data_dir, control_request(args)))
    except (ConnectionError, FileNotFoundError, OSError, ValueError) as exc:
        print(f"error: control unavailable: {exc}")
        return 1

    error = response.get("error")
    if error:
        detail = response.get("detail") or response.get("charger") or response.get("command")
        suffix = f": {detail}" if detail is not None else ""
        print(f"error: {error}{suffix}")
        return 1

    payload = response.get("response")
    status = payload.get("status") if isinstance(payload, dict) else None
    if status is None:
        print("ok")
        return 0

    print(status)
    return 0 if status == "Accepted" else 1


def run_transactions(args: argparse.Namespace) -> str:
    if args.transaction_id is not None and args.transaction_id < 0:
        raise ValueError("transaction ID must be zero or greater")
    if args.connector is not None and args.connector < 0:
        raise ValueError("--connector/--cp must be zero or greater")
    if args.limit < 1:
        raise ValueError("--limit must be at least 1")

    filtered = any((args.charger, args.connector is not None, args.id_tag, args.since, args.until))
    if args.transaction_id is not None and (args.active or args.last or filtered or args.limit != 20):
        raise ValueError("transaction ID cannot be combined with list filters or selectors")

    query = TransactionQuery(args.data_dir)
    if args.transaction_id is not None:
        view = query.get(args.transaction_id)
        return format_transaction(view) if view is not None else f"Transaction {args.transaction_id} not found."

    filters = {
        "charger": args.charger,
        "connector": args.connector,
        "id_tag": args.id_tag,
        "since": args.since,
        "until": args.until,
    }
    if args.active:
        return format_transactions(query.active(**filters))
    if args.last:
        view = query.last(**filters)
        return format_transactions([view] if view is not None else [])
    return format_transactions(query.list(limit=args.limit, **filters))


def main() -> int:
    parser, commands = build_parser()
    args = parser.parse_args()

    if args.command is None or args.command == "help":
        print_help(parser, commands, getattr(args, "topic", None))
        return 0
    if args.command == "init":
        initialize_storage(args.data_dir)
        return 0
    if args.command == "serve":
        asyncio.run(run_server(args))
        return 0
    if args.command in CONTROL_COMMANDS:
        try:
            return run_control(args)
        except ValueError as exc:
            parser.error(str(exc))
    if args.command == "status":
        print(format_status(appliance_status(args.data_dir), charger_id=args.charger, charging_only=args.charging))
        return 0
    if args.command == "transactions":
        try:
            print(run_transactions(args))
        except ValueError as exc:
            parser.error(str(exc))
        return 0
    if args.command == "events":
        if args.limit < 1:
            parser.error("--limit must be at least 1")
        try:
            rows = events_between(args.data_dir, charger_id=args.charger, since=args.since, until=args.until, limit=args.limit)
        except ValueError as exc:
            parser.error(str(exc))
        print(format_events(rows))
        return 0
    if args.command == "explain":
        if args.minutes < 1:
            parser.error("--minutes must be at least 1")
        try:
            text = explain(args.data_dir, args.charger, at=args.at, since=args.since, until=args.until, minutes=args.minutes)
        except ValueError as exc:
            parser.error(str(exc))
        print(text)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
