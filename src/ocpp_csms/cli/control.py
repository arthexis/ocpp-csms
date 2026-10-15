from __future__ import annotations

import argparse
import asyncio

from ocpp_csms.control import send_control


CONTROL_COMMANDS = ("start", "reset",)


def add_control_commands(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> dict[str, argparse.ArgumentParser]:
    """Register charger control commands with the root CLI parser."""
    add = argparse.ArgumentParser.add_argument

    start = subcommands.add_parser("start", help="Request a remote charging session")
    add(start, "charger", nargs="?", help="Charge point ID (optional when exactly one charger is connected)")
    add(start, "--cp", dest="charger_option", help="Explicit charge point ID")
    add(start, "--connector", type=int, help="Target connector ID; omit to let the charger choose")
    add(start, "--id-tag", help="RFID/idTag (default: AUTO)")
    add(start, "--force", action="store_true", help="Override busy connector guard")

    reset = subcommands.add_parser("reset", help="Request charger reset")
    add(reset, "charger", nargs="?", help="Charge point ID (optional when exactly one charger is connected)")
    add(reset, "--cp", dest="charger_option", help="Explicit charge point ID")
    add(reset, "--hard", action="store_true", help="Request a Hard reset instead of Soft")
    add(reset, "--force", action="store_true", help="Override active connector safety guard")

    return {"start": start, "reset": reset}


def _requested_charger(args: argparse.Namespace) -> str | None:
    positional = getattr(args, "charger", None)
    option = getattr(args, "charger_option", None)
    if positional and option:
        raise ValueError("charger may be provided either positionally or with --cp, not both")
    return option or positional


def control_request(args: argparse.Namespace) -> dict[str, object]:
    request: dict[str, object] = {"command": args.command}
    charger = _requested_charger(args)
    if charger is not None:
        request["charger"] = charger
    if args.command == "start":
        # RemoteStartTransaction requires idTag in OCPP 1.6 even in Allow All mode.
        request["id_tag"] = args.id_tag if args.id_tag is not None else "AUTO"
        if args.connector is not None:
            request["connector"] = args.connector
    elif args.command == "stop":
        request["transaction"] = args.transaction
    elif args.command == "reset":
        request["type"] = "Hard" if args.hard else "Soft"
    if getattr(args, "force", False):
        request["force"] = True
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
    for warning in response.get("warnings", []):
        print(f"warning: {warning}")
    payload = response.get("response")
    status = payload.get("status") if isinstance(payload, dict) else None
    if status is None:
        print("ok")
        return 0
    print(status)
    return 0 if status == "Accepted" else 1
