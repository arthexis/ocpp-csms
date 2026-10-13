"""Field maintenance OCPP 1.6J requests via the existing control socket."""
from __future__ import annotations

import argparse
import asyncio
import json

from ocpp_csms.cli.maintenance_transport import maintenance_control, MaintenanceControlError
from ocpp_csms.cli.options import add_cp, add_connector, add_json, require_nonnegative

COMMANDS = ("trigger", "avail", "availability", "unlock")
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
    for name in ("trigger", "avail", "unlock"):
        parser = subcommands.add_parser(name, aliases=["availability"] if name == "avail" else [], help={
            "trigger": "Request an OCPP message from a connected charge point",
            "avail": "Set charge point or connector availability",
            "unlock": "Request physical connector unlock",
        }[name])
        add_cp(parser, help="Charge point ID (optional with one connection)")
        add_json(parser, help="Print OCPP response as JSON")
        if name == "trigger":
            parser.add_argument("message", choices=tuple(TRIGGERS))
            add_connector(parser, help="Physical connector for status/meter triggers")
        elif name == "avail":
            parser.add_argument("action", choices=("enable", "disable"))
            add_connector(parser, default=0, help="Connector ID (0 = whole charge point)")
        else:
            add_connector(parser, required=True, help="Physical connector ID")
        commands[name] = parser
        if name == "avail":
            commands["availability"] = parser
    return commands


def maintenance_request(args):
    request = {"command": "availability" if args.command == "avail" else args.command}
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
    elif args.command in {"avail", "availability"}:
        require_nonnegative(args.connector, name="connector")
        request["connector"] = args.connector
        request["type"] = "Operative" if args.action == "enable" else "Inoperative"
    else:
        if args.connector <= 0:
            raise ValueError("connector must be a positive physical connector ID")
        request["connector"] = args.connector
    return request


def run_maintenance(args):
    try:
        payload = maintenance_control(args.data_dir, maintenance_request(args))
    except (MaintenanceControlError, ValueError) as exc:
        print(f"error: {exc}")
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
