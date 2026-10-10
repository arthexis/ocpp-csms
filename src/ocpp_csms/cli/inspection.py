"""Conservative, read-only charger capability and state comparison views."""
from __future__ import annotations

import argparse
import asyncio
import json

from ocpp_csms.control import send_control
from ocpp_csms.status import appliance_status
from ocpp_csms.transactions.query import TransactionQuery


FEATURE_KEYS = (
    ("Local authorization list", "LocalAuthListManagement"),
    ("Smart charging", "SmartCharging"),
    ("Reservation", "Reservation"),
    ("Remote trigger", "RemoteTrigger"),
    ("Firmware management", "FirmwareManagement"),
)


def add_inspection_commands(subcommands):
    result = {}
    for name, help_text in (("capabilities", "Read advertised charger feature profiles"),
                            ("reconcile", "Compare persisted connector statuses and transaction state without changes")):
        command = subcommands.add_parser(name, help=help_text)
        command.add_argument("--cp", "--charger", dest="charger", help="Filter to charge point")
        command.add_argument("-j", "--json", action="store_true", help="Print JSON")
        if name == "reconcile":
            command.add_argument("-c", "--connector", type=int, help="Filter to physical connector")
        result[name] = command
    return result


def _features(response):
    advertised = None
    for entry in response.get("configuration_key", []):
        if entry.get("key") == "SupportedFeatureProfiles":
            advertised = {part.strip() for part in (entry.get("value") or "").split(",") if part.strip()}
    return [{"feature": label, "status": ("Unknown" if advertised is None else
             "Advertised" if profile in advertised else "Not advertised")}
            for label, profile in FEATURE_KEYS]


async def _query_capabilities(data_dir, charger):
    request = {"command": "config", "keys": ["SupportedFeatureProfiles"], "force": True}
    if charger:
        request["charger"] = charger
    return await send_control(data_dir, request)


def _assessment(*, connected, status, ids):
    """Classify evidence; observations never prove a transaction has ended."""
    if not connected:
        return "Offline/uncertain"
    if len(ids) > 1:
        return "Conflict (overlapping transactions)"
    if ids and status in {"Available", "Unavailable"}:
        return "Conflict"
    if status is None:
        return "Unknown"
    return "Consistent (snapshot)"


def _reconciliation(data_dir, charger, connector):
    result = []
    snapshot = appliance_status(data_dir)
    history = TransactionQuery(data_dir).active(charger=charger, connector=connector)
    recorded = {}
    for tx in history:
        recorded.setdefault((tx.charge_point_id, tx.connector_id), []).append(tx.transaction_id)
    covered = set()
    for cp in snapshot["chargers"]:
        if charger and cp.charger_id != charger:
            continue
        for item in cp.connectors:
            if item.connector_id == 0 or (connector is not None and item.connector_id != connector):
                continue
            key = (cp.charger_id, item.connector_id)
            covered.add(key)
            ids = recorded.get(key, [])
            status = item.status
            # StatusNotification is an observation, not proof a transaction stopped.
            result.append({"cp": cp.charger_id, "connector": item.connector_id,
                           "connected": cp.connected, "observed_status": status,
                           "active_transactions": ids,
                           "assessment": _assessment(connected=cp.connected, status=status, ids=ids)})
    for key, ids in recorded.items():
        if key in covered or (charger is not None and key[0] != charger):
            continue
        result.append({"cp": key[0], "connector": key[1], "connected": False,
                       "observed_status": None, "active_transactions": ids,
                       "assessment": "Conflict (overlapping transactions)" if len(ids) > 1 else
                                     "No connector observation"})
    return result


def run_inspection(args):
    if args.command == "capabilities":
        try:
            response = asyncio.run(_query_capabilities(args.data_dir, args.charger))
        except (OSError, ValueError, ConnectionError) as exc:
            print(f"error: {exc}")
            return 1
        if response.get("error"):
            print(f"error: {response['error']}")
            return 1
        payload = response.get("response")
        if not isinstance(payload, dict):
            print("error: invalid configuration response")
            return 1
        result = _features(payload)
        if args.json:
            print(json.dumps(result))
        else:
            for item in result:
                print(f"{item['feature']:<27} {item['status']}")
            print("Not advertised is not proof of unsupported operations.")
        return 0
    if args.connector is not None and args.connector < 1:
        raise ValueError("--connector must be a positive physical connector ID")
    result = _reconciliation(args.data_dir, args.charger, args.connector)
    if args.json:
        print(json.dumps(result))
    else:
        if not result:
            print("No connector evidence to compare.")
        for row in result:
            print(f"{row['cp']} C{row['connector']}: {row['observed_status'] or 'Unknown'}; "
                  f"TX {row['active_transactions'] or '-'}; {row['assessment']}")
        print("Read-only snapshot: no charger query, transaction closure, or correction performed.")
    return 0
