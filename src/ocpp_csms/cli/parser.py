from __future__ import annotations

import argparse

from ocpp_csms.cli.control import add_control_commands
from ocpp_csms.cli.transactions import add_transaction_parser


def add_transaction_command(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    """Register the transaction command family with the root CLI parser."""
    return add_transaction_parser(subcommands)


__all__ = ["add_control_commands", "add_transaction_command"]
