"""Request charger-side OCPP diagnostics and inspect stored evidence."""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from urllib.parse import urlsplit

from ocpp_csms.cli.transactions import resolve_time
from ocpp_csms.control import send_control
from ocpp_csms.evidence.diagnostics import events_between


_ACTIONS = {"GetDiagnostics", "DiagnosticsStatusNotification"}


def add_diagnostics_command(subcommands):
    parent = subcommands.add_parser("diagnostics", help="Request charger-generated diagnostics and inspect upload evidence")
    actions = parent.add_subparsers(dest="diagnostics_action", required=True)
    request = actions.add_parser("request", help="Request charger diagnostic upload to an external destination")
    request.add_argument("--location", required=True, help="Charger-reachable HTTP(S)/FTP(S) upload URL")
    request.add_argument("--cp", "--charger", dest="charger")
    request.add_argument("--since", help="Diagnostic start time (ISO-8601 or relative, e.g. 1d)")
    request.add_argument("--until", help="Diagnostic stop time (ISO-8601, relative, or 'now')")
    request.add_argument("--retries", type=int)
    request.add_argument("--retry-interval", type=int, help="Retry interval in seconds")
    request.add_argument("-j", "--json", action="store_true")
    history = actions.add_parser("history", help="Read stored diagnostic request and notification evidence")
    history.add_argument("--cp", "--charger", dest="charger")
    history.add_argument("--since", help="Limit to events after ISO-8601 or relative timestamp")
    history.add_argument("-n", "--limit", type=int, default=100)
    history.add_argument("-j", "--json", action="store_true")
    return parent


def _iso(value, *, now):
    if value is None:
        return None
    if value.strip().lower() == "now":
        return now.isoformat()
    return resolve_time(value, now=now).isoformat()


def _request(args):
    parsed = urlsplit(args.location)
    if parsed.scheme.lower() not in {"http", "https", "ftp", "ftps"} or not parsed.hostname or parsed.fragment:
        raise ValueError("--location must be an absolute HTTP(S) or FTP(S) URL without a fragment")
    if args.retries is not None and args.retries < 0:
        raise ValueError("--retries must be non-negative")
    if args.retry_interval is not None and args.retry_interval < 0:
        raise ValueError("--retry-interval must be non-negative")
    now = datetime.now(timezone.utc)
    start, stop = _iso(args.since, now=now), _iso(args.until, now=now)
    if start and stop and start > stop:
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
    if args.diagnostics_action == "history":
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
        response = asyncio.run(send_control(args.data_dir, _request(args)))
    except (ConnectionError, OSError, ValueError) as exc:
        print(f"error: {exc}")
        return 1
    if response.get("error"):
        print(f"error: {response['error']}: {response.get('detail') or ''}".rstrip())
        return 1
    payload = response.get("response")
    if not isinstance(payload, dict):
        print("error: invalid GetDiagnostics response")
        return 1
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"Request acknowledged; suggested filename: {payload.get('file_name', payload.get('fileName')) or '(not provided)'}")
        print("Upload completion is not confirmed. Inspect diagnostics history or events.")
    return 0
