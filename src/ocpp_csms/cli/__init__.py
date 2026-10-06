"""Command-line interface package for ocpp-csms.

Command families are being migrated here incrementally. During the migration,
the existing application module remains the parser skeleton until it is retired.
"""

from __future__ import annotations

import argparse
import sys

from ocpp_csms import app
from ocpp_csms.cli.appliance import APPLIANCE_COMMANDS, run_appliance
from ocpp_csms.cli.config import configuration_request, is_config_download, run_config_download, run_configuration
from ocpp_csms.cli.control import CONTROL_COMMANDS, control_request, run_control
from ocpp_csms.cli.diagnostics import DIAGNOSTIC_COMMANDS, run_diagnostic
from ocpp_csms.cli.profile import run_profile
from ocpp_csms.cli.transactions import add_transaction_parser, run_transactions


def build_parser() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    """Build the current CLI, including command families already moved here."""
    parser, commands = app.build_parser()
    from ocpp_csms.cli.config import add_config_download_arguments

    add_config_download_arguments(commands["config"])
    return parser, commands


def main() -> int:
    parser, commands = build_parser()
    args = parser.parse_args(sys.argv[1:])
    if args.command is None or args.command == "help":
        app.print_help(parser, commands, getattr(args, "topic", None))
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
        return run_configuration(args)
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
    "run_appliance",
    "run_config_download",
    "run_configuration",
    "run_control",
    "run_diagnostic",
    "run_profile",
    "run_transactions",
]
