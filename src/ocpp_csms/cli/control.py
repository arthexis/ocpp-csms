from __future__ import annotations

import argparse
import asyncio

from ocpp_csms.control import send_control


CONTROL_COMMANDS = ("start", "stop", "reboot")


def add_control_commands(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> dict[str, argparse.ArgumentParser]:
    """Register charger control commands with the root CLI parser."""
    add = argparse.ArgumentParser.add_argument

    start = subcommands.add_parser("start", help="Request remote transaction start")
    add(start, "charger", nargs="?", help="Charge point ID (optional when exactly one charger is connected)")
    add(start, "-c", "--charger", dest="charger_option", help="Explicit charge point ID")
    add(start, "--connector", "--cp", dest="connector", type=int, help="Connector ID")
    add(start, "--id-tag", required=True, help="OCPP idTag for the remote start")

    stop = subcommands.add_parser("stop", help="Request remote transaction stop")
    add(stop, "charger", nargs="?", help="Charge point ID (optional when exactly one charger is connected)")
    add(stop, "-c", "--charger", dest="charger_option", help="Explicit charge point ID")
    add(stop, "-t", "--transaction", "--txn", dest="transaction", type=int, required=True, help="OCPP transaction ID")

    reboot = subcommands.add_parser("reboot", help="Request charger reset")
    add(reboot, "charger", nargs="?", help="Charge point ID (optional when exactly one charger is connected)")
    add(reboot, "-c", "--charger", dest="charger_option", help="Explicit charge point ID")
    add(reboot, "--hard", action="store_true", help="Request a Hard reset instead of Soft")

    return {"start": start, "stop": stop, "reboot": reboot}


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
