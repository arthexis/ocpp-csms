"""Request charger-side OCPP diagnostics and inspect stored evidence."""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from urllib.parse import urlsplit

from ocpp_csms.cli.transactions import resolve_time
from ocpp_csms.cli.maintenance_transport import maintenance_control, MaintenanceControlError
from ocpp_csms.evidence.diagnostics import events_between


_ACTIONS = {"GetDiagnostics", "DiagnosticsStatusNotification"}


def add_diagnostics_command(subcommands):
    parser = subcommands.add_parser("diagnose", help="Inspect diagnostic history or explicitly request charger diagnostics")
    parser.add_argument("--cp", "--charger", dest="charger", help="Charge point ID")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--now", action="store_true", help="Request a new charger diagnostics upload")
    mode.add_argument("--list", action="store_true", help="Explicitly list stored diagnostics (default)")
    parser.add_argument("--location", help="Charger-reachable HTTP(S)/FTP(S) upload URL; required with --now")
    parser.add_argument("--since", help="Diagnostic start time or history filter (ISO-8601 or relative)")
    parser.add_argument("--until", help="Diagnostic stop time for --now")
    parser.add_argument("--retries", type=int, help="Upload retries for --now")
    parser.add_argument("--retry-interval", type=int, help="Retry interval in seconds for --now")
    parser.add_argument("-n", "--limit", type=int, default=100, help="Maximum history events (default: 100)")
    parser.add_argument("-j", "--json", action="store_true")
    return parser


def _iso(value, *, now):
    if value is None:
        return None
    if value.strip().lower() == "now":
        return now.isoformat()
    return resolve_time(value, now=now).isoformat()


def _request(args):
    if not args.location:
        raise ValueError("--location is required with --now")
    parsed = urlsplit(args.location)
    if parsed.scheme.lower() not in {"http", "https", "ftp", "ftps"} or not parsed.hostname or parsed.fragment:
        raise ValueError("--location must be an absolute HTTP(S) or FTP(S) URL without a fragment")
    if args.retries is not None and args.retries < 0:
        raise ValueError("--retries must be non-negative")
    if args.retry_interval is not None and args.retry_interval < 0:
        raise ValueError("--retry-interval must be non-negative")
    now = datetime.now(timezone.utc)
    start, stop = _iso(args.since, now=now), _iso(args.until, now=now)
    if start and stop and datetime.fromisoformat(start) > datetime.fromisoformat(stop):
        raise ValueError("--since must not be after --until")
    request = {"command": "get_diagnostics", "location": args.location}
    if args.charger:
        request["charger"] = args.charger
    for key, value in (("start_time", start), ("stop_time", stop), ("retries", args.retries), ("retry_interval", args.retry_interval)):
        if value is not None:
            request[key] = value
    return request


def _history(args):
    if args.limit < 1:
        raise ValueError("--limit must be at least one")
    since = resolve_time(args.since).isoformat() if args.since else None
    rows = events_between(args.data_dir, charger_id=args.charger, since=since, limit=None)
    result = []
    for row in rows:
        if row["kind"] != "ocpp" or row["action"] not in _ACTIONS:
            continue
        try:
            payload = json.loads(row["payload"] or "{}")
        except (ValueError, TypeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        result.append({"at": row["occurred_at"], "cp": row["charger_id"],
                       "action": row["action"], "direction": row["direction"],
                       "status": payload.get("status"), "file_name": payload.get("file_name", payload.get("fileName")),
                       "event_id": row["id"]})
    return result[-args.limit:]


def run_diagnostics(args):
    if not args.now:
        if args.location or args.until or args.retries is not None or args.retry_interval is not None:
            raise ValueError("--location, --until, --retries and --retry-interval require --now")
        result = _history(args)
        if args.json:
            print(json.dumps({"events": result}, ensure_ascii=False))
        else:
            if not result:
                print("No charger diagnostic evidence.")
            for item in result:
                print(f"{item['at']} {item['cp']} {item['action']} ({item['direction']}) "
                      f"{item['status'] or item['file_name'] or '-'}")
            print("Notifications are observations; without OCPP message IDs, request/notification association is not guaranteed.")
        return 0
    try:
        payload = maintenance_control(args.data_dir, _request(args), malformed="invalid GetDiagnostics response")
    except (MaintenanceControlError, ValueError) as exc:
        print(f"error: {exc}")
        return 1
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"Request acknowledged; suggested filename: {payload.get('file_name', payload.get('fileName')) or '(not provided)'}")
        print("Upload completion is not confirmed. Inspect diagnostics history or events.")
    return 0
