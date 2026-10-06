"""Command-line interface for the OCPP CSMS appliance."""

from __future__ import annotations

import argparse
import sys

from ocpp_csms.cli.appliance import APPLIANCE_COMMANDS, add_appliance_commands, run_appliance
from ocpp_csms.cli.config import add_config_command, configuration_request, is_config_download, run_config_download, run_configuration
from ocpp_csms.cli.control import CONTROL_COMMANDS, add_control_commands, control_request, run_control
from ocpp_csms.cli.diagnostics import DIAGNOSTIC_COMMANDS, add_diagnostic_commands, run_diagnostic
from ocpp_csms.cli.profile import add_profile_command, run_profile
from ocpp_csms.cli.transactions import add_transaction_parser, run_transactions
from ocpp_csms.transactions import default_data_dir


def build_parser() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    """Build the complete command-line parser from explicit command modules."""
    parser = argparse.ArgumentParser(prog="ocpp-csms", description="Small OCPP 1.6J CSMS appliance.")
    parser.add_argument("--data-dir", default=str(default_data_dir()), help="Writable data directory (default: %(default)s)")
    subcommands = parser.add_subparsers(dest="command")

    commands: dict[str, argparse.ArgumentParser] = {}
    commands.update(add_appliance_commands(subcommands))
    commands.update(add_control_commands(subcommands))
    commands["config"] = add_config_command(subcommands)
    commands["profile"] = add_profile_command(subcommands)
    commands.update(add_diagnostic_commands(subcommands))
    transactions = add_transaction_parser(subcommands)
    commands["transactions"] = transactions
    commands["txn"] = transactions

    help_parser = subcommands.add_parser("help", help="Show commands and parameters")
    topics = tuple(commands)
    help_parser.add_argument("topic", nargs="?", choices=topics)
    return parser, commands


def print_help(
    parser: argparse.ArgumentParser,
    commands: dict[str, argparse.ArgumentParser],
    topic: str | None = None,
) -> None:
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


def main() -> int:
    parser, commands = build_parser()
    args = parser.parse_args(sys.argv[1:])
    if args.command is None or args.command == "help":
        print_help(parser, commands, getattr(args, "topic", None))
        return 0
    if args.command in APPLIANCE_COMMANDS:
        return run_appliance(args)
    if args.command in CONTROL_COMMANDS:
        try:
            return run_control(args)
        except ValueError as exc:
            parser.error(str(exc))
    if args.command == "config":
        if is_config_download(args):
            return run_config_download(args)
        try:
            return run_configuration(args)
        except ValueError as exc:
            parser.error(str(exc))
    if args.command == "profile":
        try:
            return run_profile(args)
        except ValueError as exc:
            parser.error(str(exc))
    if args.command in DIAGNOSTIC_COMMANDS:
        try:
            return run_diagnostic(args)
        except ValueError as exc:
            parser.error(str(exc))
    if args.command == "transactions":
        try:
            print(run_transactions(args))
        except ValueError as exc:
            parser.error(str(exc))
        return 0
    return 2


__all__ = [
    "add_transaction_parser",
    "build_parser",
    "configuration_request",
    "control_request",
    "main",
    "print_help",
    "run_appliance",
    "run_config_download",
    "run_configuration",
    "run_control",
    "run_diagnostic",
    "run_profile",
    "run_transactions",
]
