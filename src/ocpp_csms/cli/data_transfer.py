"""Operator-facing vendor DataTransfer without vendor-specific execution."""
from __future__ import annotations

import argparse
import asyncio
import json

from ocpp_csms.control import send_control


def add_data_transfer_command(subcommands):
    root = subcommands.add_parser("data-transfer", help="Send vendor-specific OCPP DataTransfer")
    actions = root.add_subparsers(dest="transfer_action", required=True)
    send = actions.add_parser("send", help="Send vendor DataTransfer to a connected charge point")
    send.add_argument("--cp", "--charger", dest="charger")
    send.add_argument("--vendor", required=True, help="OCPP vendorId")
    send.add_argument("--message-id", help="Optional vendor messageId")
    send.add_argument("--data", help="Optional opaque string payload (including JSON text)")
    send.add_argument("-j", "--json", action="store_true")
    return root


def transfer_request(args):
    if not 1 <= len(args.vendor) <= 255:
        raise ValueError("--vendor must contain 1–255 characters")
    if args.message_id is not None and not 1 <= len(args.message_id) <= 50:
        raise ValueError("--message-id must contain 1–50 characters")
    result = {"command": "data_transfer", "vendor_id": args.vendor}
    if args.message_id is not None:
        result["message_id"] = args.message_id
    if args.data is not None:
        result["data"] = args.data
    if args.charger:
        result["charger"] = args.charger
    return result


def run_data_transfer(args):
    try:
        result = asyncio.run(send_control(args.data_dir, transfer_request(args)))
    except (OSError, ConnectionError, ValueError) as exc:
        print(f"error: {exc}")
        return 1
    if result.get("error"):
        print(f"error: {result['error']}")
        return 1
    response = result.get("response")
    if not isinstance(response, dict):
        print("error: invalid DataTransfer response")
        return 1
    if args.json:
        print(json.dumps(response, ensure_ascii=False))
    else:
        print(f"Status: {response.get('status', 'Unknown')}")
        if response.get("data") is not None:
            print(f"Data: {response['data']}")
    return 0 if response.get("status") == "Accepted" else 1
