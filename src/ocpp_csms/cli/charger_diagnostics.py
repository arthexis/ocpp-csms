"""Request charger-side OCPP diagnostics and inspect stored evidence."""
from __future__ import annotations

import json
import subprocess
import threading
import time
from datetime import datetime, timezone

from ocpp_csms.cli.transactions import resolve_time
from ocpp_csms.cli.maintenance_validation import validate_transfer_url, validate_retry_options
from ocpp_csms.cli.maintenance_transport import maintenance_control, MaintenanceControlError
from ocpp_csms.evidence.diagnostics import events_between
from ocpp_csms import diagnostic_upload


_ACTIONS = {"GetDiagnostics", "DiagnosticsStatusNotification"}


def add_diagnostics_command(subcommands):
    parser = subcommands.add_parser("diagnose", help="Inspect diagnostic history or explicitly request charger diagnostics")
    parser.add_argument("--cp", "--charger", dest="charger", help="Charge point ID")
    parser.add_argument("--list", action="store_true", help="Explicitly list stored diagnostics (default)")
    parser.add_argument("--location", help="Charger-reachable HTTP(S)/FTP(S) upload URL; required to request an upload")
    parser.add_argument("--since", help="Diagnostic start time or history filter (ISO-8601 or relative)")
    parser.add_argument("--until", help="Diagnostic stop time for upload requests")
    parser.add_argument("--retries", type=int, help="Upload retries for upload requests")
    parser.add_argument("--retry-interval", type=int, help="Retry interval in seconds for upload requests")
    parser.add_argument("-n", "--limit", type=int, default=100, help="Maximum history events (default: 100)")
    parser.add_argument("-j", "--json", action="store_true")
    ops = parser.add_subparsers(dest="diagnostic_operation")
    request = ops.add_parser("request", help="Request diagnostics using the configured HTTPS receiver")
    request.add_argument("--cp", dest="charger", help="Charge point ID (default: unique connected charger)")
    request.add_argument("--since", help="Charger diagnostics start time")
    request.add_argument("--until", help="Charger diagnostics end time")
    request.add_argument("--retries", type=int)
    request.add_argument("--retry-interval", type=int)
    request.add_argument("--limit", type=int, default=None, help="Maximum seconds to wait for OCPP response (default: unlimited)")
    request.add_argument("--follow", action="store_true", help="Follow charger status and file receipt until completion or failure")
    request.add_argument("--json", action="store_true")
    for name in ("status", "list", "show", "prune"):
        op = ops.add_parser(name, help="Inspect locally received diagnostic uploads")
        op.add_argument("--cp", dest="charger", help="Filter by charge point ID")
        op.add_argument("--json", action="store_true")
        if name == "show":
            op.add_argument("--id", help="Upload request ID; default: latest")
    return parser


def run_https_diagnostics(args):
    op = args.diagnostic_operation
    if op == "request":
        if args.limit is not None and args.limit < 1:
            print("error: --limit must be a positive number of seconds")
            return 1
        # Dispatch in a daemon worker so an independently reported Uploading
        # can acknowledge the request even if the CALLRESULT never arrives.
        # The server owns the outgoing OCPP task after the control socket closes.
        record, location = diagnostic_upload.prepare(args.data_dir, args.charger)
        args.location = location
        try:
            request_payload = _request(args)
        except ValueError:
            diagnostic_upload.mark_timeout(args.data_dir, record["id"])
            print("Invalid diagnostic request options.")
            return 1

        completion = threading.Event()
        outcome = {}
        def send():
            try:
                outcome["payload"] = maintenance_control(
                    args.data_dir, request_payload, malformed="invalid GetDiagnostics response"
                )
                if not isinstance(outcome["payload"], dict):
                    raise ValueError("invalid diagnostics response")
                diagnostic_upload.record_response(args.data_dir, record["id"], outcome["payload"])
            except (MaintenanceControlError, ValueError, OSError):
                # Never print original exception; it can contain the bearer URL.
                outcome["error"] = "response_unconfirmed"
            finally:
                completion.set()

        threading.Thread(target=send, daemon=True).start()
        started = time.monotonic()
        acknowledged = False
        seen = set()
        last_result = None
        try:
            while True:
                entries = diagnostic_upload.list_requests(args.data_dir, charger=args.charger)
                current = next((item for item in entries if item["id"] == record["id"]), None)
                if current:
                    for notification in current.get("notifications", []):
                        marker = (notification["at"], notification["status"])
                        if marker not in seen:
                            seen.add(marker)
                            print(f"{notification['at']} {notification['status']}", flush=True)
                    if current["result"] != last_result:
                        last_result = current["result"]
                        if current["result"] == "received":
                            print("Diagnostic file received by the HTTPS server.", flush=True)
                        elif current["result"] == "charger_uploadfailed":
                            print("Charger reports UploadFailed.", flush=True)
                    if current["result"] in ("charger_uploading", "charger_uploaded", "charger_uploadfailed", "received"):
                        acknowledged = True
                if completion.is_set() and "payload" in outcome:
                    acknowledged = True
                if not acknowledged:
                    if completion.is_set() and "error" in outcome:
                        diagnostic_upload.mark_timeout(args.data_dir, record["id"])
                        print("GetDiagnostics response unconfirmed; upload destination remains available.")
                        return 1
                    if args.limit is not None and time.monotonic() - started >= args.limit:
                        print("Initial acknowledgement not observed before --limit; upload destination remains available.")
                        return 1
                elif not args.follow:
                    print("Initial acknowledgement observed; use diagnose status to inspect later upload results.")
                    return 0
                if args.follow and current and current["result"] in ("received", "charger_uploadfailed"):
                    return 0 if current["result"] == "received" else 1
                if args.follow and current and current["result"] == "expired":
                    print("Diagnostic upload destination expired without a received file.")
                    return 1
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("Stopped following; the upload request remains recorded.")
            return 130
    if op == "prune":
        print(f"Expired diagnostic requests removed: {diagnostic_upload.remove_expired(args.data_dir)}")
        return 0
    records = diagnostic_upload.list_requests(args.data_dir, charger=args.charger)
    if op == "status":
        records = records[:1]
    elif op == "show" and args.id:
        records = [r for r in records if r["id"] == args.id]
    elif op == "show":
        records = records[:1]
    if args.json:
        # IDs are capabilities for the internal directory, never emit them in JSON.
        print(json.dumps({"requests": [{k: v for k, v in r.items() if k != "id"} for r in records]}, ensure_ascii=False))
        return 0
    if not records:
        print("No local HTTPS diagnostic requests.")
        return 0
    for entry in records:
        if entry.get("notifications"):
            print("  Charger evidence (time-correlated): " + " -> ".join(n["status"] for n in entry["notifications"]))
        print(f"{entry['created_at']} {entry.get('charger') or '-'} {entry['result']} "
              f"files={len(entry['files'])} suggested={entry.get('suggested_file') or '-'}")
        if op == "show":
            for f in entry["files"]:
                print(f"  {f['name']} ({f['bytes']} bytes) — {diagnostic_upload.ROOT / entry['id'] / f['name']}")
                for line in diagnostic_upload.inspect_file(diagnostic_upload.ROOT / entry["id"] / f["name"]):
                    print(f"    {line}")
    return 0


def _iso(value, *, now):
    if value is None:
        return None
    if value.strip().lower() == "now":
        return now.isoformat()
    return resolve_time(value, now=now).isoformat()


def _request(args):
    if not args.location:
        raise ValueError("--location is required to request an upload")
    validate_transfer_url(args.location, error="--location must be an absolute HTTP(S) or FTP(S) URL without a fragment")
    validate_retry_options(args.retries, args.retry_interval)
    now = datetime.now(timezone.utc)
    start, stop = _iso(args.since, now=now), _iso(args.until, now=now)
    if start and stop and datetime.fromisoformat(start) > datetime.fromisoformat(stop):
        raise ValueError("--since must not be after --until")
    request = {"command": "get_diagnostics", "location": args.location}
    if args.charger:
        request["charger"] = args.charger
    if getattr(args, "diagnostic_operation", None) == "request":
        request["response_timeout"] = args.limit
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
    if getattr(args, "diagnostic_operation", None):
        return run_https_diagnostics(args)
    if not args.location or args.list:
        if args.list and args.location:
            raise ValueError("--list cannot be combined with --location")
        if args.until or args.retries is not None or args.retry_interval is not None:
            raise ValueError("--until, --retries and --retry-interval require --location")
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
