from __future__ import annotations

import argparse

from ocpp_csms.output import emit_json
from ocpp_csms.status import appliance_status, format_status
from ocpp_csms.status_contract import status_contract


CHARGER_COMMANDS = ("charger", "chargers", "cp", "cps")


def add_charger_commands(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> dict[str, argparse.ArgumentParser]:
    add = argparse.ArgumentParser.add_argument

    charger = subcommands.add_parser(
        "charger", aliases=["cp"], help="Show one charge point and its connectors"
    )
    charger.set_defaults(command="charger")
    add(charger, "charger", help="Charge point ID")
    add(charger, "-j", "--json", action="store_true", help="Print machine-readable charge point status")

    chargers = subcommands.add_parser(
        "chargers", aliases=["cps"], help="List known charge points"
    )
    chargers.set_defaults(command="chargers")
    add(chargers, "--charging", action="store_true", help="Show only charge points with active transactions")
    add(chargers, "-j", "--json", action="store_true", help="Print machine-readable charge point status")

    return {name: charger if name in ("charger", "cp") else chargers for name in CHARGER_COMMANDS}


def run_chargers(args: argparse.Namespace) -> int:
    status = appliance_status(args.data_dir)
    charger_id = args.charger if args.command == "charger" else None
    charging_only = getattr(args, "charging", False)
    if args.json:
        emit_json(status_contract(status, charger_id=charger_id, charging_only=charging_only))
    else:
        print(format_status(status, charger_id=charger_id, charging_only=charging_only, appliance=False))
    return 0
