from __future__ import annotations

import argparse
import asyncio
import logging

from ocpp_csms.events import EventStore
from ocpp_csms.server import CSMSServer
from ocpp_csms.transactions import TransactionArchive


APPLIANCE_COMMANDS = ("init", "serve")


def add_appliance_commands(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> dict[str, argparse.ArgumentParser]:
    """Register appliance lifecycle commands."""
    add = argparse.ArgumentParser.add_argument

    init = subcommands.add_parser("init", help="Initialize appliance storage")
    serve = subcommands.add_parser("serve", help="Run the OCPP server")
    add(serve, "--host", default="0.0.0.0")
    add(serve, "--port", type=int, default=9000)
    add(serve, "--log-level", default="INFO")
    return {"init": init, "serve": serve}


def initialize_storage(data_dir: str) -> None:
    TransactionArchive(data_dir)
    EventStore(data_dir)


async def run_server(args: argparse.Namespace) -> None:
    logging.basicConfig(level=args.log_level.upper())
    await CSMSServer(
        host=args.host,
        port=args.port,
        transactions=TransactionArchive(args.data_dir),
        events=EventStore(args.data_dir),
    ).serve_forever()


def run_appliance(args: argparse.Namespace) -> int:
    if args.command == "init":
        initialize_storage(args.data_dir)
        return 0
    if args.command == "serve":
        asyncio.run(run_server(args))
        return 0
    raise ValueError(f"unknown appliance command: {args.command}")
