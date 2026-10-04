from __future__ import annotations

import argparse
import asyncio
import json
import logging

from ocpp_csms.control import send_control
from ocpp_csms.diagnostics import events_between, explain, format_events, transaction_events
from ocpp_csms.events import EventStore
from ocpp_csms.profile_templates import (
    build_profile,
    format_profile_template_help,
    format_profile_template_list,
    get_profile_template,
)
from ocpp_csms.server import CSMSServer
from ocpp_csms.status import appliance_status, format_status
from ocpp_csms.transaction_cli import format_transaction, format_transactions
from ocpp_csms.transaction_query import TransactionQuery
from ocpp_csms.transactions import TransactionArchive, default_data_dir


CONTROL_COMMANDS = ("start", "stop", "reboot")
PROFILE_PURPOSES = ("ChargePointMaxProfile", "TxDefaultProfile", "TxProfile")


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
    add(start, "charger", nargs="?", help="Charge point ID (optional when exactly one charger is connected)")
    add(start, "--charger", dest="charger_option", help="Explicit charge point ID")
    add(start, "--connector", "--cp", dest="connector", type=int, help="Connector ID")
    add(start, "--id-tag", required=True, help="OCPP idTag for the remote start")

    stop = subcommands.add_parser("stop", help="Request remote transaction stop")
    add(stop, "charger", nargs="?", help="Charge point ID (optional when exactly one charger is connected)")
    add(stop, "--charger", dest="charger_option", help="Explicit charge point ID")
    add(stop, "--transaction", "--txn", dest="transaction", type=int, required=True, help="OCPP transaction ID")

    reboot = subcommands.add_parser("reboot", help="Request charger reset")
    add(reboot, "charger", nargs="?", help="Charge point ID (optional when exactly one charger is connected)")
    add(reboot, "--charger", dest="charger_option", help="Explicit charge point ID")
    add(reboot, "--hard", action="store_true", help="Request a Hard reset instead of Soft")

    config = subcommands.add_parser("config", help="Read or change charger configuration")
    add(config, "--charger", help="Explicit charge point ID when more than one charger is connected")
    add(config, "items", nargs="*", metavar="KEY", help="Keys to read, or: set KEY VALUE")
    add(config, "-f", "--force", action="store_true", help="Operate even with an active transaction")

    profile = subcommands.add_parser("profile", help="Inspect and apply Smart Charging profiles")
    profile_subcommands = profile.add_subparsers(dest="profile_command")
    profile_subcommands.add_parser("list", help="List built-in profile templates")
    profile_help = profile_subcommands.add_parser("help", help="Explain a built-in profile template")
    add(profile_help, "template", help="Built-in profile template name")
    profile_set = profile_subcommands.add_parser("set", help="Apply a built-in profile template")
    add(profile_set, "template", help="Built-in profile template name")
    add(profile_set, "--charger", help="Explicit charge point ID when more than one charger is connected")
    add(profile_set, "--watts", type=int, required=True, help="Maximum charging power in watts")
    profile_composite = profile_subcommands.add_parser("composite", help="Show the charger's effective composite schedule")
    add(profile_composite, "--charger", help="Explicit charge point ID when more than one charger is connected")
    add(profile_composite, "--connector", "--cp", dest="connector", type=int, default=0, help="Connector ID (default: %(default)s)")
    add(profile_composite, "--duration", type=int, default=3600, help="Schedule duration in seconds (default: %(default)s)")
    add(profile_composite, "--json", action="store_true", help="Print the OCPP response as JSON")
    profile_clear = profile_subcommands.add_parser("clear", help="Clear Smart Charging profiles from the charger")
    add(profile_clear, "--charger", help="Explicit charge point ID when more than one charger is connected")
    add(profile_clear, "--id", dest="profile_id", type=int, help="Clear one chargingProfileId")
    add(profile_clear, "--connector", "--cp", dest="connector", type=int, help="Filter by connector ID")
    add(profile_clear, "--purpose", choices=PROFILE_PURPOSES, help="Filter by charging profile purpose")
    add(profile_clear, "--stack-level", type=int, help="Filter by stack level")

    status = subcommands.add_parser("status", help="Show appliance or charger status")
    add(status, "charger", nargs="?", help="Charge point ID")
    add(status, "--charging", action="store_true", help="Show only charging chargers")

    transactions = subcommands.add_parser("transactions", aliases=["txn"], help="Inspect archived transactions")
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
    add(transactions, "--events", action="store_true", help="Show OCPP timeline for a transaction ID")

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
    topics = ("init", "serve", "start", "stop", "reboot", "config", "profile", "status", "transactions", "txn", "events", "explain")
    add(help_parser, "topic", nargs="?", choices=topics)
    commands = {"init": init, "serve": serve, "start": start, "stop": stop, "reboot": reboot, "config": config, "profile": profile, "status": status, "transactions": transactions, "txn": transactions, "events": events, "explain": explain_parser}
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
    await CSMSServer(host=args.host, port=args.port, transactions=TransactionArchive(args.data_dir), events=EventStore(args.data_dir)).serve_forever()


def initialize_storage(data_dir: str) -> None:
    TransactionArchive(data_dir)
    EventStore(data_dir)


def _requested_charger(args: argparse.Namespace) -> str | None:
    positional = getattr(args, "charger", None)
    option = getattr(args, "charger_option", None)
    if positional and option:
        raise ValueError("charger may be provided either positionally or with --charger, not both")
    return option or positional


def control_request(args: argparse.Namespace) -> dict[str, object]:
    request: dict[str, object] = {"command": args.command}
    charger = _requested_charger(args)
    if charger is not None:
        request["charger"] = charger
    if args.command == "start":
        request["id_tag"] = args.id_tag
        if args.connector is not None:
            request["connector"] = args.connector
    elif args.command == "stop":
        request["transaction"] = args.transaction
    elif args.command == "reboot":
        request["type"] = "Hard" if args.hard else "Soft"
    return request


def configuration_request(args: argparse.Namespace) -> dict[str, object]:
    items = list(args.items)
    if items and items[0] == "set":
        if len(items) != 3:
            raise ValueError("config set requires KEY and VALUE")
        request: dict[str, object] = {
            "command": "config_set",
            "key": items[1],
            "value": items[2],
            "force": bool(args.force),
        }
    else:
        request = {"command": "config", "force": bool(args.force)}
        if items:
            request["keys"] = items
    if args.charger is not None:
        request["charger"] = args.charger
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


def _format_configuration(payload: dict[str, object]) -> str:
    rows = payload.get("configuration_key") or []
    unknown = payload.get("unknown_key") or []
    if not isinstance(rows, list) or not isinstance(unknown, list):
        raise ValueError("invalid configuration response")
    lines = ["KEY\tACCESS\tVALUE"]
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid configuration response")
        key = row.get("key")
        readonly = row.get("readonly")
        value = row.get("value")
        if not isinstance(key, str) or not isinstance(readonly, bool) or (value is not None and not isinstance(value, str)):
            raise ValueError("invalid configuration response")
        lines.append(f"{key}\t{'R' if readonly else 'RW'}\t{value or ''}")
    for key in unknown:
        if not isinstance(key, str):
            raise ValueError("invalid configuration response")
        lines.append(f"Unknown: {key}")
    return "\n".join(lines)


def run_configuration(args: argparse.Namespace) -> int:
    try:
        request = configuration_request(args)
        response = asyncio.run(send_control(args.data_dir, request))
    except (ConnectionError, FileNotFoundError, OSError, ValueError) as exc:
        print(f"error: control unavailable: {exc}")
        return 1
    error = response.get("error")
    if error:
        detail = response.get("detail") or response.get("charger")
        suffix = f": {detail}" if detail is not None else ""
        print(f"error: {error}{suffix}")
        return 1
    payload = response.get("response")
    if not isinstance(payload, dict):
        return 1
    if request["command"] == "config_set":
        change = payload.get("change")
        readback = payload.get("readback")
        if not isinstance(change, dict) or not isinstance(readback, dict):
            return 1
        status = change.get("status")
        print(status or "Unknown")
        try:
            print(_format_configuration(readback))
        except ValueError:
            return 1
        return 0 if status in {"Accepted", "RebootRequired"} else 1
    try:
        print(_format_configuration(payload))
    except ValueError:
        return 1
    return 0


def _profile_set_request(args: argparse.Namespace) -> dict[str, object]:
    connector, profile = build_profile(args.template, watts=args.watts)
    request: dict[str, object] = {
        "command": "set_charging_profile",
        "connector": connector,
        "profile": profile,
    }
    if args.charger is not None:
        request["charger"] = args.charger
    return request


def _profile_composite_request(args: argparse.Namespace) -> dict[str, object]:
    if args.connector < 0:
        raise ValueError("--connector/--cp must be zero or greater")
    if args.duration < 1:
        raise ValueError("--duration must be at least 1 second")
    request: dict[str, object] = {
        "command": "get_composite_schedule",
        "connector": args.connector,
        "duration": args.duration,
    }
    if args.charger is not None:
        request["charger"] = args.charger
    return request


def _profile_clear_request(args: argparse.Namespace) -> dict[str, object]:
    if args.profile_id is not None and args.profile_id < 0:
        raise ValueError("--id must be zero or greater")
    if args.connector is not None and args.connector < 0:
        raise ValueError("--connector/--cp must be zero or greater")
    if args.stack_level is not None and args.stack_level < 0:
        raise ValueError("--stack-level must be zero or greater")
    request: dict[str, object] = {"command": "clear_charging_profile"}
    if args.charger is not None:
        request["charger"] = args.charger
    if args.profile_id is not None:
        request["id"] = args.profile_id
    if args.connector is not None:
        request["connector"] = args.connector
    if args.purpose is not None:
        request["purpose"] = args.purpose
    if args.stack_level is not None:
        request["stack_level"] = args.stack_level
    return request


def _schedule_value(mapping: dict[str, object], snake: str, camel: str) -> object:
    return mapping.get(snake, mapping.get(camel))


def _format_single_composite_schedule(payload: dict[str, object]) -> str:
    status = payload.get("status")
    if status != "Accepted":
        return str(status or "Unknown")
    connector = payload.get("connector_id", payload.get("connectorId"))
    start = payload.get("schedule_start", payload.get("scheduleStart"))
    schedule = payload.get("charging_schedule", payload.get("chargingSchedule"))
    if not isinstance(schedule, dict):
        raise ValueError("invalid composite schedule response")
    unit = _schedule_value(schedule, "charging_rate_unit", "chargingRateUnit")
    periods = _schedule_value(schedule, "charging_schedule_period", "chargingSchedulePeriod")
    if not isinstance(periods, list):
        raise ValueError("invalid composite schedule response")
    lines = [f"Connector: {connector}", f"Schedule start: {start}", f"Rate unit: {unit}", "Periods:"]
    for period in periods:
        if not isinstance(period, dict):
            raise ValueError("invalid composite schedule response")
        offset = _schedule_value(period, "start_period", "startPeriod")
        limit = period.get("limit")
        phases = _schedule_value(period, "number_phases", "numberPhases")
        line = f"  +{offset}s\t{limit} {unit}"
        if phases is not None:
            line += f"\t{phases} phase(s)"
        lines.append(line)
    return "\n".join(lines)


def _format_composite_schedule(payload: dict[str, object]) -> str:
    if payload.get("compatibility_fallback") != "physical_connectors":
        return _format_single_composite_schedule(payload)
    schedules = payload.get("schedules")
    if not isinstance(schedules, list):
        raise ValueError("invalid composite schedule fallback response")
    lines = [
        "Charger does not support aggregate composite schedule on connector 0.",
        "Showing physical connectors instead.",
    ]
    for item in schedules:
        if not isinstance(item, dict):
            raise ValueError("invalid composite schedule fallback response")
        connector_id = item.get("connector_id")
        response = item.get("response")
        if not isinstance(response, dict):
            raise ValueError("invalid composite schedule fallback response")
        lines.extend(["", f"Connector {connector_id}", _format_single_composite_schedule(response)])
    return "\n".join(lines)


def _send_profile_request(args: argparse.Namespace, request: dict[str, object]) -> tuple[int, dict[str, object] | None]:
    try:
        response = asyncio.run(send_control(args.data_dir, request))
    except (ConnectionError, FileNotFoundError, OSError, ValueError) as exc:
        print(f"error: {exc}")
        return 1, None
    error = response.get("error")
    if error:
        detail = response.get("detail") or response.get("charger")
        suffix = f": {detail}" if detail is not None else ""
        print(f"error: {error}{suffix}")
        return 1, None
    payload = response.get("response")
    if not isinstance(payload, dict):
        return 1, None
    return 0, payload


def run_profile(args: argparse.Namespace) -> int:
    if args.profile_command == "list":
        print(format_profile_template_list())
        return 0
    if args.profile_command == "help":
        template = get_profile_template(args.template)
        if template is None:
            print(f"error: unknown profile template: {args.template}")
            return 1
        print(format_profile_template_help(template))
        return 0
    if args.profile_command == "set":
        try:
            request = _profile_set_request(args)
        except ValueError as exc:
            print(f"error: {exc}")
            return 1
        code, payload = _send_profile_request(args, request)
        if code or payload is None:
            return 1
        status = payload.get("status")
        print(status or "Unknown")
        return 0 if status == "Accepted" else 1
    if args.profile_command == "composite":
        try:
            request = _profile_composite_request(args)
        except ValueError as exc:
            print(f"error: {exc}")
            return 1
        code, payload = _send_profile_request(args, request)
        if code or payload is None:
            return 1
        if args.json:
            print(json.dumps(payload, sort_keys=True))
        else:
            try:
                print(_format_composite_schedule(payload))
            except ValueError as exc:
                print(f"error: {exc}")
                return 1
        return 0 if payload.get("status") == "Accepted" else 1
    if args.profile_command == "clear":
        try:
            request = _profile_clear_request(args)
        except ValueError as exc:
            print(f"error: {exc}")
            return 1
        code, payload = _send_profile_request(args, request)
        if code or payload is None:
            return 1
        status = payload.get("status")
        print(status or "Unknown")
        return 0 if status == "Accepted" else 1
    raise ValueError("profile requires a subcommand")


def run_transactions(args: argparse.Namespace) -> str:
    if args.transaction_id is not None and args.transaction_id < 0:
        raise ValueError("transaction ID must be zero or greater")
    if args.connector is not None and args.connector < 0:
        raise ValueError("--connector/--cp must be zero or greater")
    if args.limit < 1:
        raise ValueError("--limit must be at least 1")
    filtered = any((args.charger, args.connector is not None, args.id_tag, args.since, args.until))
    if args.events and args.transaction_id is None:
        raise ValueError("--events requires a transaction ID")
    if args.transaction_id is not None and (args.active or args.last or filtered or args.limit != 20):
        raise ValueError("transaction ID cannot be combined with list filters or selectors")
    query = TransactionQuery(args.data_dir)
    if args.transaction_id is not None:
        view = query.get(args.transaction_id)
        if view is None:
            return f"Transaction {args.transaction_id} not found."
        detail = format_transaction(view)
        if not args.events:
            return detail
        timeline = format_events(transaction_events(args.data_dir, args.transaction_id), heading=f"Transaction {args.transaction_id} OCPP events")
        return f"{detail}\n\n{timeline}"
    filters = {"charger": args.charger, "connector": args.connector, "id_tag": args.id_tag, "since": args.since, "until": args.until}
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
    if args.command == "config":
        try:
            return run_configuration(args)
        except ValueError as exc:
            parser.error(str(exc))
    if args.command == "profile":
        try:
            return run_profile(args)
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