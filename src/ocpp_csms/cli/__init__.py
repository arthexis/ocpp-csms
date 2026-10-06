"""Command-line interface package for ocpp-csms.

Command families are being migrated here incrementally. During the migration,
the existing application module remains the implementation for commands that
have not moved yet.
"""

from __future__ import annotations

import argparse
import sys

from ocpp_csms import app
from ocpp_csms.cli.config import add_config_download_arguments, is_config_download, run_config_download
from ocpp_csms.cli.transactions import add_transaction_parser, run_transactions


def build_parser() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    """Build the current CLI, including command families already moved here."""
    parser, commands = app.build_parser()
    add_config_download_arguments(commands["config"])
    return parser, commands


def main() -> int:
    parser, _ = build_parser()
    args = parser.parse_args(sys.argv[1:])
    if args.command == "config" and is_config_download(args):
        return run_config_download(args)
    # Command families not yet migrated still use app.main(), which preserves
    # their existing parsing and dispatch behavior during the staged move.
    return app.main()


__all__ = [
    "add_transaction_parser",
    "build_parser",
    "main",
    "run_config_download",
    "run_transactions",
]
