from __future__ import annotations

import argparse
import asyncio
import logging

from ocpp_csms.events import EventStore
from ocpp_csms.server import CSMSServer
from ocpp_csms.transactions import TransactionArchive, default_data_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the OCPP CSMS server.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument(
        "--data-dir",
        default=str(default_data_dir()),
        help="Writable data directory (default: %(default)s)",
    )
    return parser.parse_args()


async def run() -> None:
    args = parse_args()
    logging.basicConfig(level=args.log_level.upper())
    transactions = TransactionArchive(args.data_dir)
    events = EventStore(args.data_dir)
    server = CSMSServer(
        host=args.host,
        port=args.port,
        transactions=transactions,
        events=events,
    )
    await server.serve_forever()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
