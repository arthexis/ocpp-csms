"""Field maintenance OCPP 1.6J requests via the existing control socket."""
from __future__ import annotations

import argparse
import asyncio
import json

from ocpp_csms.control import send_control

COMMANDS = ("trigger", "availability", "unlock")
TRIGGERS = {
    "boot": "BootNotification",
    "heartbeat": "Heartbeat",
    "status": "StatusNotification",
    "meter": "MeterValues",
    "diagnostics": "DiagnosticsStatusNotification",
    "firmware": "FirmwareStatusNotification",
}


def add_maintenance_commands(subcommands):
    commands = {}
    for name in COMMANDS:
        parser = subcommands.add_parser(name, help={
            "trigger": "Request an OCPP message from a connected charge point",
            "availability": "Set charge point or connector availability",
            "unlock": "Request physical connector unlock",
        }[name])
        parser.add_argument("--cp", "--charger", dest="charger", help="Charge point ID (optional with one connection)")
        parser.add_argument("-j", "--json", action="store_true", help="Print OCPP response as JSON")
        if name == "trigger":
            parser.add_argument("message", choices=tuple(TRIGGERS))
            parser.add_argument("-c", "--connector", type=int, help="Physical connector for status/meter triggers")
        elif name == "availability":
            parser.add_argument("action", choices=("enable", "disable"))
            parser.add_argument("-c", "--connector", type=int, default=0, help="Connector ID (0 = whole charge point)")
        else:
            parser.add_argument("-c", "--connector", type=int, required=True, help="Physical connector ID")
        commands[name] = parser
    return commands


def maintenance_request(args):
    request = {"command": args.command}
    if args.charger is not None:
        request["charger"] = args.charger
    if args.command == "trigger":
        if args.connector is not None and args.connector <= 0:
            raise ValueError("connector must be a positive physical connector ID")
        if args.connector is not None and args.message not in {"status", "meter"}:
            raise ValueError("--connector only applies to status and meter")
        request["message"] = TRIGGERS[args.message]
        if args.connector is not None:
            request["connector"] = args.connector
    elif args.command == "availability":
        if args.connector < 0:
            raise ValueError("connector must be zero or greater")
        request["connector"] = args.connector
        request["type"] = "Operative" if args.action == "enable" else "Inoperative"
    else:
        if args.connector <= 0:
            raise ValueError("connector must be a positive physical connector ID")
        request["connector"] = args.connector
    return request


def run_maintenance(args):
    try:
        response = asyncio.run(send_control(args.data_dir, maintenance_request(args)))
    except (ConnectionError, FileNotFoundError, OSError, ValueError) as exc:
        print(f"error: {exc}")
        return 1
    if response.get("error"):
        print(f"error: {response['error']}: {response.get('detail') or response.get('charger') or ''}".rstrip())
        return 1
    payload = response.get("response")
    if not isinstance(payload, dict):
        print("error: invalid OCPP response")
        return 1
    status = payload.get("status")
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print(status or "Unknown")
        if status == "Scheduled":
            print("Pending: charger will change availability after its active transaction ends.")
        elif status == "Accepted":
            print("Request accepted; physical outcome is not yet confirmed.")
    return 0 if status in {"Accepted", "Scheduled"} else 1
