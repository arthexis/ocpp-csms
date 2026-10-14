from __future__ import annotations

import argparse
import asyncio

from ocpp_csms.control import send_control


CONTROL_COMMANDS = ("reset",)


def add_control_commands(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> dict[str, argparse.ArgumentParser]:
    """Register charger control commands with the root CLI parser."""
    add = argparse.ArgumentParser.add_argument

    reset = subcommands.add_parser("reset", help="Request charger reset")
    add(reset, "charger", nargs="?", help="Charge point ID (optional when exactly one charger is connected)")
    add(reset, "--cp", dest="charger_option", help="Explicit charge point ID")
    add(reset, "--hard", action="store_true", help="Request a Hard reset instead of Soft")
    add(reset, "--force", action="store_true", help="Override active connector safety guard")
    _add_timing_options(reset)

    return {"reset": reset}


def _add_timing_options(parser: argparse.ArgumentParser) -> None:
    timing = parser.add_mutually_exclusive_group(required=False)
    timing.add_argument("--now", action="store_true", help="Execute as soon as the command is accepted")
    timing.add_argument("--after", type=int, metavar="SECONDS", help="Wait SECONDS, then attempt the command")
    timing.add_argument(
        "--within",
        type=int,
        metavar="SECONDS",
        help="Execute when unblocked, waiting up to SECONDS for an active transaction to finish",
    )


def _requested_charger(args: argparse.Namespace) -> str | None:
    positional = getattr(args, "charger", None)
    option = getattr(args, "charger_option", None)
    if positional and option:
        raise ValueError("charger may be provided either positionally or with --cp, not both")
    return option or positional


def _timing_request(args: argparse.Namespace) -> dict[str, object]:
    if args.now:
        return {"timing": "now"}
    if args.after is not None:
        return {"timing": "after", "seconds": args.after}
    if args.within is not None:
        return {"timing": "within", "seconds": args.within}
    return {"timing": "now"}


def control_request(args: argparse.Namespace) -> dict[str, object]:
    request: dict[str, object] = {"command": args.command, **_timing_request(args)}
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
    seconds = args.after if args.after is not None else args.within
    if seconds is not None and seconds <= 0:
        raise ValueError("--after/--within must be greater than zero")
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
