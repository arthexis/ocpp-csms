"""Manage charge-point HTTP Basic credentials without exposing passwords in arguments."""
from __future__ import annotations

import getpass
import json

from ocpp_csms.auth import auth_path, create_basic, load_auth


def add_auth_command(subcommands):
    parser = subcommands.add_parser("auth", help="Manage WebSocket authentication")
    actions = parser.add_subparsers(dest="auth_action", required=True)
    basic = actions.add_parser("basic", help="Configure HTTP Basic credentials for one charge point")
    basic.add_argument("--cp", required=True, help="Charge-point ID and Basic username")
    actions.add_parser("status", help="Show authentication mode without secrets")
    return parser


def run_auth(args):
    if args.auth_action == "basic":
        first = getpass.getpass("Basic password: ")
        second = getpass.getpass("Confirm password: ")
        if not first or first != second:
            raise ValueError("passwords must match and cannot be empty")
        create_basic(args.cp, first)
        print(f"Basic authentication configured for {args.cp}; restart ocpp-csms to apply.")
        return 0
    if args.auth_action == "status":
        config = load_auth()
        print(json.dumps({"mode": config["mode"], "charge_point": config.get("username")}, sort_keys=True))
        return 0
    raise ValueError("unknown auth action")
