from __future__ import annotations

import argparse

from ocpp_csms.cli.transactions import add_transaction_parser


def add_transaction_command(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    """Register the transaction command family with the root CLI parser."""
    return add_transaction_parser(subcommands)
