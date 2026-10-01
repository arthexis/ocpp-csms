from __future__ import annotations

import argparse
import asyncio
import logging

from ocpp_csms.composition import build_server


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the OCPP CSMS server.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


async def run() -> None:
    args = parse_args()
    logging.basicConfig(level=args.log_level.upper())
    server = build_server(host=args.host, port=args.port)
    await server.serve_forever()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
