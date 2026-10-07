"""Command-line interface for the OCPP CSMS appliance."""
from __future__ import annotations
import argparse
import sys
from ocpp_csms.cli.appliance import APPLIANCE_COMMANDS, add_appliance_commands, run_appliance
from ocpp_csms.cli.chargers import CHARGER_COMMANDS, add_charger_commands, run_chargers
from ocpp_csms.cli.config import add_config_command, configuration_request, is_config_download, run_config_download, run_configuration
from ocpp_csms.cli.control import CONTROL_COMMANDS, add_control_commands, control_request, run_control
from ocpp_csms.cli.diagnostics import DIAGNOSTIC_COMMANDS, add_diagnostic_commands, run_diagnostic
from ocpp_csms.cli.energy import add_energy_command, run_energy
from ocpp_csms.cli.json_contracts import run_transactions_json
from ocpp_csms.cli.profile import add_profile_command, run_profile
from ocpp_csms.cli.transactions import TRANSACTION_COMMANDS, add_transaction_parser, run_transactions
from ocpp_csms.transactions import default_data_dir


def build_parser() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    parser = argparse.ArgumentParser(prog="ocpp-csms", description="Small OCPP 1.6J CSMS appliance.")
    parser.add_argument("--data-dir", default=str(default_data_dir()), help="Writable data directory (default: %(default)s")
    subcommands = parser.add_subparsers(dest="command")
    commands: dict[str, argparse.ArgumentParser] = {}
    commands.update(add_appliance_commands(subcommands))
    commands.update(add_control_commands(subcommands))
    commands.update(add_charger_commands(subcommands))
    commands["config"] = add_config_command(subcommands)
    commands["profile"] = add_profile_command(subcommands)
    commands["energy"] = add_energy_command(subcommands)
    commands.update(add_diagnostic_commands(subcommands))
    transactions = add_transaction_parser(subcommands)
    transactions.add_argument("-j", "--json", action="store_true", help="Print the stable machine-readable transaction contract")
    for name in TRANSACTION_COMMANDS:
        commands[name] = transactions
    help_parser = subcommands.add_parser("help", help="Show commands and parameters")
    help_parser.add_argument("topic", nargs="?", choices=tuple(commands))
    return parser, commands


def print_help(parser, commands, topic=None) -> None:
    if topic:
        commands[topic].print_help(); return
    parser.print_help()
    printed: set[int] = set()
    for command in commands.values():
        if id(command) in printed: continue
        printed.add(id(command)); print(); command.print_help()


def main() -> int:
    parser, commands = build_parser(); args = parser.parse_args(sys.argv[1:])
    if args.command is None or args.command == "help": print_help(parser, commands, getattr(args, "topic", None)); return 0
    try:
        if args.command in APPLIANCE_COMMANDS: return run_appliance(args)
        if args.command in CONTROL_COMMANDS: return run_control(args)
        if args.command in ("charger", "chargers"): return run_chargers(args)
        if args.command == "config": return run_config_download(args) if is_config_download(args) else run_configuration(args)
        if args.command == "profile": return run_profile(args)
        if args.command == "energy": return run_energy(args)
        if args.command in DIAGNOSTIC_COMMANDS: return run_diagnostic(args)
        if args.command == "transactions":
            if args.json: return run_transactions_json(args)
            print(run_transactions(args)); return 0
    except ValueError as exc:
        parser.error(str(exc))
    return 2


__all__ = ["add_transaction_parser", "build_parser", "configuration_request", "control_request", "main", "print_help", "run_appliance", "run_config_download", "run_configuration", "run_control", "run_chargers", "run_diagnostic", "run_energy", "run_profile", "run_transactions"]
