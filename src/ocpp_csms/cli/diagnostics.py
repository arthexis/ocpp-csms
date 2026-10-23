from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone

from ocpp_csms.cli.transactions import resolve_time
from ocpp_csms.cli.options import add_cp, add_json

from ocpp_csms.evidence.diagnostics import events_between, explain, format_events, fault_events, format_faults
from ocpp_csms.evidence.contracts import events_contract, raw_events_contract
from ocpp_csms.evidence.alerts import classify_events, alerts_contract, format_alerts
from ocpp_csms.output import emit_json
from ocpp_csms.status import appliance_status, format_status
from ocpp_csms.status_contract import status_contract

DIAGNOSTIC_COMMANDS = ("status", "events", "faults", "alerts", "explain")


def add_diagnostic_commands(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> dict[str, argparse.ArgumentParser]:
    add = argparse.ArgumentParser.add_argument
    status = subcommands.add_parser("status", help="Show appliance or charger status")
    add_cp(status, help="Filter by charge point ID")
    add(status, "--charging", action="store_true", help="Show only charging chargers")
    add(status, "-j", "--json", action="store_true", help="Print the stable machine-readable status contract")

    events = subcommands.add_parser("events", help="Show recorded events")
    add_cp(events, help="Filter by charge point ID")
    add(events, "--tx", dest="transaction", type=int, help="Filter by one OCPP transaction ID")
    add(events, "--txn", dest="transactions", type=_parse_transaction_ids, help="Filter by comma-separated OCPP transaction IDs")
    add(events, "--since", help="Lower timestamp bound (ISO-8601 or relative, e.g. 3d)")
    add(events, "--until", help="Upper timestamp bound (ISO-8601 or relative, e.g. 12h)")
    limit_group = events.add_mutually_exclusive_group()
    limit_group.add_argument("-n", "--limit", help="Event count or duration (default: 200 without --since; e.g. 1d, 72h, 1w)")
    limit_group.add_argument("-N", "--no-limit", action="store_true", help="Return all matching events, without the default 200-event cap")
    add(events, "-j", "--json", action="store_true", help="Print the stable machine-readable event contract")
    add(events, "--raw", action="store_true", help="Include raw diagnostic payloads (requires --json)")
    add(events, "-f", "--follow", action="store_true", help="Follow newly recorded events until Ctrl+C (JSON uses JSONL)")
    add(events, "--exclude", help="Comma-separated, case-insensitive action substrings to hide (e.g. He,Me)")
    add(events, "--only", help="Comma-separated, case-insensitive action substrings to include")
    add(events, "-L", "--local-time", action="store_true", help="Display event timestamps in the host local timezone")
    add(events, "--detail", action="store_true", help="Expand additional event fields in the grouped timeline")
    add(events, "--verbose", action="store_true", help="Show every original event with full payload, without grouping")

    faults = subcommands.add_parser("faults", help="Show recorded connector faults with original charger timestamps")
    add_cp(faults, help="Filter by charge point ID (default: all chargers)")
    add(faults, "--since", help="CSMS receive time lower bound (ISO-8601 or relative)")
    add(faults, "--until", help="CSMS receive time upper bound (ISO-8601 or relative)")
    add(faults, "-n", "--limit", type=int, default=20, help="Maximum matching fault notifications (default: 20)")
    add_json(faults, help="Print recorded fault details as JSON")

    alerts = subcommands.add_parser("alerts", help="Show exceptional events without grouping")
    add_cp(alerts, help="Filter by charge point ID")
    add(alerts, "--transaction", "--txn", dest="transaction", type=int)
    add(alerts, "--since", help="Lower timestamp bound")
    add(alerts, "--until", help="Upper timestamp bound")
    alert_limit_group = alerts.add_mutually_exclusive_group()
    alert_limit_group.add_argument("-n", "--limit", default="100", help="Alert count or duration")
    alert_limit_group.add_argument("-N", "--no-limit", action="store_true")
    add(alerts, "-j", "--json", action="store_true")
    add(alerts, "--raw", action="store_true", help="Include diagnostic details (requires --json)")
    add(alerts, "--verbose", action="store_true", help="Include diagnostic details")
    
    explain_parser = subcommands.add_parser("explain", help="Show charger evidence for a time window")
    add_cp(explain_parser, required=True, help="Charge point ID")
    add(explain_parser, "--at", help="ISO-8601 center timestamp")
    add(explain_parser, "--since", help="ISO-8601 lower timestamp bound")
    add(explain_parser, "--until", help="ISO-8601 upper timestamp bound")
    add(explain_parser, "--minutes", type=int, default=10, help="Minutes around --at")
    return {"status": status, "events": events, "faults": faults, "alerts": alerts, "explain": explain_parser}


def run_status(args: argparse.Namespace) -> int:
    status = appliance_status(args.data_dir)
    if args.json:
        emit_json(status_contract(status, charger_id=args.charger, charging_only=args.charging))
    else:
        print(format_status(status, charger_id=args.charger, charging_only=args.charging))
    return 0


def _parse_event_limit(value: str | int) -> tuple[int | None, str | None]:
    """Return either a positive event count or a positive duration."""
    value = str(value).strip()
    if re.fullmatch(r"[0-9]+", value):
        count = int(value)
        if count < 1:
            raise ValueError("--limit must be at least 1")
        return count, None
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([smhdw])", value, re.IGNORECASE)
    if match and float(match.group(1)) > 0:
        return None, value
    raise ValueError("--limit must be a positive event count or duration (e.g. 1d)")


def _parse_transaction_ids(value: str) -> tuple[int, ...]:
    parts = value.split(",")
    if any(not part.strip().isdigit() for part in parts):
        raise argparse.ArgumentTypeError("--txn requires comma-separated non-negative transaction IDs")
    return tuple(dict.fromkeys(int(part.strip()) for part in parts))


def _event_transaction_filter(args: argparse.Namespace) -> int | tuple[int, ...] | None:
    single = getattr(args, "transaction", None)
    multiple = getattr(args, "transactions", None)
    if single is not None and multiple is not None:
        raise ValueError("--tx and --txn cannot be combined")
    if single is not None and single < 0:
        raise ValueError("--tx must be zero or greater")
    return multiple if multiple is not None else single


def parse_action_patterns(value: str) -> tuple[str, ...]:
    patterns = tuple(part.strip().casefold() for part in value.split(","))
    if not all(patterns):
        raise ValueError("--exclude/--only requires nonempty comma-separated message name fragments")
    return patterns


def run_events(args: argparse.Namespace) -> int:
    count, duration = (None, None) if getattr(args, 'no_limit', False) or (args.since is not None and args.limit is None) else _parse_event_limit(args.limit if args.limit is not None else '200')
    transaction_filter = _event_transaction_filter(args)
    if args.raw and not args.json:
        raise ValueError("--raw requires --json")
    if args.verbose and args.json:
        raise ValueError("--verbose cannot be combined with --json")
    if args.detail and args.json:
        raise ValueError("--detail cannot be combined with --json")
    now = datetime.now(timezone.utc)
    since = resolve_time(args.since, now=now) if args.since else None
    until = resolve_time(args.until, now=now) if args.until else None
    if duration is not None:
        end = until or now
        # The duration is relative to the end of the requested window.
        window_start = resolve_time(duration, now=end)
        since = max(since, window_start) if since is not None else window_start
    if since is not None and until is not None and since > until:
        raise ValueError("--since must be earlier than or equal to --until")
    if getattr(args, "follow", False):
        from ocpp_csms.cli.event_follow import _cursor
        cursor = _cursor(args.data_dir)
    filters = {}
    for option in ("exclude", "only"):
        value = getattr(args, option, None)
        if value is not None:
            filters[option] = parse_action_patterns(value)
    rows = events_between(
        args.data_dir, charger_id=args.charger, transaction_id=transaction_filter,
        since=since.isoformat() if since else None,
        until=until.isoformat() if until else None, limit=count, **filters,
    )
    if getattr(args, "follow", False):
        from ocpp_csms.cli.event_follow import follow_events
        args.transaction = transaction_filter
        return follow_events(args, rows, cursor=cursor, since=since.isoformat() if since else None,
                             until=until.isoformat() if until else None)
    if args.json:
        emit_json(raw_events_contract(rows) if args.raw else events_contract(rows))
    else:
        print(format_events(rows, verbose=args.verbose, detail=args.detail, local_time=args.local_time))
    return 0



def run_faults(args: argparse.Namespace) -> int:
    since = resolve_time(args.since).isoformat() if args.since else None
    until = resolve_time(args.until).isoformat() if args.until else None
    faults = fault_events(args.data_dir, charger_id=args.charger, since=since,
                          until=until, limit=args.limit)
    if args.json:
        emit_json({"faults": faults})
    else:
        print(format_faults(faults))
    return 0


def run_alerts(args: argparse.Namespace) -> int:
    """Use the same time/count semantics as events, limiting after classification."""
    count, duration = (None, None) if args.no_limit else _parse_event_limit(args.limit)
    if args.transaction is not None and args.transaction < 0:
        raise ValueError("--transaction/--txn must be zero or greater")
    if args.raw and not args.json:
        raise ValueError("--raw requires --json")
    if args.verbose and args.json:
        raise ValueError("--verbose cannot be combined with --json")
    now = datetime.now(timezone.utc)
    since = resolve_time(args.since, now=now) if args.since else None
    until = resolve_time(args.until, now=now) if args.until else None
    if duration is not None:
        end = until or now
        window_start = resolve_time(duration, now=end)
        since = max(since, window_start) if since is not None else window_start
    if since is not None and until is not None and since > until:
        raise ValueError("--since must be earlier than or equal to --until")
    rows = events_between(
        args.data_dir, charger_id=args.charger,
        transaction_id=args.transaction,
        since=since.isoformat() if since else None,
        until=until.isoformat() if until else None, limit=None,
    )
    alerts = classify_events(rows)
    if count is not None:
        alerts = alerts[-count:]
    if args.json:
        emit_json(alerts_contract(alerts))
    else:
        print(format_alerts(alerts))
    return 0


def run_explain(args: argparse.Namespace) -> int:
    print(explain(args.data_dir, args.charger, at=args.at, since=args.since, until=args.until, minutes=args.minutes))
    return 0


def run_diagnostic(args: argparse.Namespace) -> int:
    if args.command == "status": return run_status(args)
    if args.command == "events": return run_events(args)
    if args.command == "faults": return run_faults(args)
    if args.command == "alerts": return run_alerts(args)
    if args.command == "explain": return run_explain(args)
    raise ValueError(f"unknown diagnostic command: {args.command}")
