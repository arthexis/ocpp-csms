"""OCPP ReserveNow and CancelReservation operator commands."""
from __future__ import annotations
import argparse
import asyncio
import json
from datetime import datetime, timezone
from ocpp_csms.cli.transactions import resolve_time
from ocpp_csms.control import send_control

def _create_args(parser):
    parser.add_argument("--cp", "--charger", dest="charger")
    parser.add_argument("-c", "--connector", type=int, required=True, help="Connector ID (0 = any connector)")
    parser.add_argument("--rfid", required=True, help="RFID / OCPP idTag")
    parser.add_argument("--until", required=True, help="Reservation expiry (ISO-8601 with timezone, or relative e.g. 1h)")
    parser.add_argument("--id", "--reservation-id", dest="reservation_id", required=True, type=int, help="Unique OCPP reservation ID")
    parser.add_argument("--parent-rfid", dest="parent_id_tag")
    parser.add_argument("-j", "--json", action="store_true")

def _cancel_args(parser):
    parser.add_argument("reservation_id", type=int)
    parser.add_argument("--cp", "--charger", dest="charger")
    parser.add_argument("-j", "--json", action="store_true")

def add_reservation_commands(subcommands):
    reserve = subcommands.add_parser("reserve", help="Reserve a connector via OCPP ReserveNow")
    _create_args(reserve)
    reservation = subcommands.add_parser("reservation", help="Manage OCPP reservations")
    sub = reservation.add_subparsers(dest="reservation_action", required=True)
    _create_args(sub.add_parser("create", help="Alias for reserve"))
    _cancel_args(sub.add_parser("cancel", help="Cancel a charger reservation"))
    return {"reserve": reserve, "reservation": reservation}

def reservation_request(args):
    action = getattr(args, "reservation_action", "create")
    if action == "cancel":
        if args.reservation_id <= 0:
            raise ValueError("reservation ID must be positive")
        result = {"command": "cancel_reservation", "reservation_id": args.reservation_id}
    else:
        if args.connector < 0:
            raise ValueError("connector must be zero or greater")
        if args.reservation_id <= 0:
            raise ValueError("reservation ID must be positive")
        if not 1 <= len(args.rfid) <= 20:
            raise ValueError("RFID must contain 1–20 characters")
        if args.parent_id_tag is not None and not 1 <= len(args.parent_id_tag) <= 20:
            raise ValueError("parent RFID must contain 1–20 characters")
        expiry = resolve_time(args.until, now=datetime.now(timezone.utc))
        if expiry <= datetime.now(timezone.utc):
            raise ValueError("reservation expiry must be in the future")
        result = {"command": "reserve", "connector": args.connector, "reservation_id": args.reservation_id,
                  "id_tag": args.rfid, "expiry_date": expiry.isoformat()}
        if args.parent_id_tag:
            result["parent_id_tag"] = args.parent_id_tag
    if args.charger:
        result["charger"] = args.charger
    return result

def run_reservation(args):
    try:
        result = asyncio.run(send_control(args.data_dir, reservation_request(args)))
    except (OSError, ValueError, ConnectionError) as exc:
        print(f"error: {exc}")
        return 1
    if result.get("error"):
        print(f"error: {result['error']}")
        return 1
    payload = result.get("response")
    if not isinstance(payload, dict):
        print("error: invalid OCPP response")
        return 1
    if args.json:
        print(json.dumps(payload))
    else:
        print(f"Reservation: {payload.get('status', 'Unknown')}")
    return 0 if payload.get("status") == "Accepted" else 1
