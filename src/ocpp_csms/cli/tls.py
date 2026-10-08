"""TLS configuration CLI (listener management follows in Chunk 2B)."""
from __future__ import annotations

import argparse
import json

from ocpp_csms import tls_config


def add_tls_command(subcommands: argparse._SubParsersAction) -> argparse.ArgumentParser:
    parser = subcommands.add_parser("tls", help="Manage TLS configuration and readiness")
    parser.add_argument("--config-path", default=str(tls_config.DEFAULT_CONFIG), help=argparse.SUPPRESS)
    actions = parser.add_subparsers(dest="tls_command", required=True)
    config = actions.add_parser("config", help="Register TLS certificate and key paths")
    config.add_argument("--cert", required=True)
    config.add_argument("--key", required=True)
    config.add_argument("--hostname", required=True)
    config.add_argument("--port", type=int, default=9443)
    actions.add_parser("status", help="Show persistent TLS configuration")
    check = actions.add_parser("check", help="Check certificate and key readiness")
    check.add_argument("--ws-port", type=int, default=9000)
    return parser


def run_tls(args: argparse.Namespace) -> int:
    path = args.config_path
    if args.tls_command == "config":
        previous = tls_config.read_config(path)
        config = tls_config.TLSConfig(
            cert=args.cert, key=args.key, hostname=args.hostname, port=args.port,
            enabled=previous.enabled if previous else False,
        )
        tls_config.write_config(config, path)
        result = tls_config.status(path)
    elif args.tls_command == "status":
        result = tls_config.status(path)
    elif args.tls_command == "check":
        config = tls_config.read_config(path)
        if config is None:
            result = {"ready": False, "errors": ["tls_not_configured"], "listener": "not_implemented"}
        else:
            result = tls_config.check_config(config, ws_port=args.ws_port)
    else:
        raise ValueError("invalid_tls_command")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if args.tls_command == "check" and not result["ready"] else 0
