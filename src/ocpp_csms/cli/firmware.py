"""OCPP 1.6 firmware update requests and read-only status history."""
from __future__ import annotations
import argparse
import asyncio
import json
from datetime import datetime, timezone
from urllib.parse import urlsplit

from ocpp_csms.cli.transactions import resolve_time
from ocpp_csms.control import send_control
from ocpp_csms.evidence.diagnostics import events_between

ACTIONS = {"UpdateFirmware", "FirmwareStatusNotification"}

def add_firmware_command(subcommands):
    root = subcommands.add_parser("firmware", help="Request charger firmware update and inspect reported progress")
    sub = root.add_subparsers(dest="firmware_action", required=True)
    update = sub.add_parser("update", help="Send an OCPP UpdateFirmware request")
    update.add_argument("--location", required=True, help="Charger-reachable firmware URL")
    update.add_argument("--cp", "--charger", dest="charger")
    timing = update.add_mutually_exclusive_group(required=True)
    timing.add_argument("--now", action="store_true", help="Explicitly request immediate retrieval")
    timing.add_argument("--at", help="Scheduled retrieval time, ISO-8601 with timezone")
    update.add_argument("--retries", type=int)
    update.add_argument("--retry-interval", type=int)
    update.add_argument("--confirm", action="store_true", help="Acknowledge potential service disruption")
    update.add_argument("-j", "--json", action="store_true")
    for name in ("status", "history"):
        parser = sub.add_parser(name, help="Show observed firmware status" if name == "status" else "Show firmware OCPP evidence")
        parser.add_argument("--cp", "--charger", dest="charger")
        parser.add_argument("--since")
        parser.add_argument("-n", "--limit", type=int, default=100)
        parser.add_argument("-j", "--json", action="store_true")
    return root

def firmware_request(args, *, now=None):
    if not args.confirm:
        raise ValueError("firmware update requires --confirm (may interrupt charging)")
    try:
        parts = urlsplit(args.location)
        if parts.scheme.lower() not in {"http", "https", "ftp", "ftps"} or not parts.hostname or parts.fragment:
            raise ValueError("invalid firmware URL")
    except ValueError as exc:
        raise ValueError("invalid firmware URL") from exc
    if args.retries is not None and args.retries < 0:
        raise ValueError("--retries must be non-negative")
    if args.retry_interval is not None and args.retry_interval < 0:
        raise ValueError("--retry-interval must be non-negative")
    current = now or datetime.now(timezone.utc)
    if args.now:
        date = current
    else:
        date = datetime.fromisoformat(args.at.replace("Z", "+00:00"))
        if date.tzinfo is None:
            raise ValueError("--at needs an explicit timezone")
        if date <= current:
            raise ValueError("--at must be in the future; use --now instead")
    result = {"command": "update_firmware", "location": args.location,
              "retrieve_date": date.astimezone(timezone.utc).isoformat(), "immediate": bool(args.now)}
    if args.charger:
        result["charger"] = args.charger
    if args.retries is not None:
        result["retries"] = args.retries
    if args.retry_interval is not None:
        result["retry_interval"] = args.retry_interval
    return result

def firmware_events(args):
    if args.limit < 1:
        raise ValueError("--limit must be positive")
    since = resolve_time(args.since).isoformat() if args.since else None
    rows = events_between(args.data_dir, charger_id=args.charger, since=since, limit=None)
    output = []
    for row in rows:
        if row["kind"] != "ocpp" or row["action"] not in ACTIONS:
            continue
        try:
            payload = json.loads(row["payload"] or "{}")
        except (ValueError, TypeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        output.append({"id": row["id"], "at": row["occurred_at"], "cp": row["charger_id"],
                       "action": row["action"], "direction": row["direction"],
                       "status": payload.get("status"), "retrieve_date": payload.get("retrieve_date")})
    if args.firmware_action == "status":
        latest = {}
        for event in output:
            if event["action"] == "FirmwareStatusNotification" and event["direction"] == "in":
                latest[event["cp"]] = event
        return list(latest.values())
    return output[-args.limit:]

def run_firmware(args):
    if args.firmware_action != "update":
        items = firmware_events(args)
        if args.json:
            print(json.dumps({"events": items}, ensure_ascii=False))
        else:
            if not items:
                print("No recorded firmware status.")
            for item in items:
                print(f"{item['at']} {item['cp']} {item['action']} {item['status'] or item['direction']}")
            print("Recorded observations; not confirmation of firmware compatibility or successful installation.")
        return 0
    try:
        response = asyncio.run(send_control(args.data_dir, firmware_request(args)))
    except (ConnectionError, OSError, ValueError) as exc:
        print(f"error: {exc}")
        return 1
    if response.get("error"):
        print(f"error: {response['error']}")
        return 1
    payload = response.get("response")
    if not isinstance(payload, dict):
        print("error: malformed OCPP acknowledgment")
        return 1
    if args.json:
        print(json.dumps(payload))
    else:
        print("UpdateFirmware acknowledged. Download and installation are not confirmed.")
        print("Inspect firmware status or history for charger notifications.")
    return 0
