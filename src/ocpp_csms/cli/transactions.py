from __future__ import annotations

import argparse
import re
from datetime import datetime, timedelta, timezone

from ocpp_csms.diagnostics import format_events, transaction_events
from ocpp_csms.transaction_cli import format_transaction, format_transactions
from ocpp_csms.transaction_query import TransactionQuery

TRANSACTION_COMMANDS = ("transactions", "transaction", "txns", "txn")
_RELATIVE_TIME = re.compile(r"^(\d+(?:\.\d+)?)([SMHDW])$", re.IGNORECASE)
_RELATIVE_SECONDS = {"S": 1, "M": 60, "H": 3600, "D": 86400, "W": 604800}


def add_transaction_parser(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> argparse.ArgumentParser:
    add = argparse.ArgumentParser.add_argument
    transactions = subcommands.add_parser("transactions", aliases=["transaction", "txns", "txn"], help="Inspect archived transactions")
    transactions.set_defaults(command="transactions")
    add(transactions, "transaction_id", nargs="?", type=int, help="Transaction ID for detailed inspection")
    selection = transactions.add_mutually_exclusive_group()
    selection.add_argument("--active", action="store_true", help="Show only active transactions")
    selection.add_argument("--last", action="store_true", help="Show the most recent transaction, including active")
    add(transactions, "--cp", "--charger", dest="charger", help="Filter by charge point ID")
    add(transactions, "-c", "--connector", dest="connector", type=int, help="Filter by connector ID")
    add(transactions, "--id-tag", help="Filter by OCPP idTag")
    add(transactions, "--since", help="Lower timestamp bound (ISO-8601 or relative, e.g. 7D)")
    add(transactions, "--until", help="Upper timestamp bound (ISO-8601 or relative, e.g. 2H)")
    add(transactions, "--between", nargs=2, metavar=("START", "END"), help="Activity between two ISO-8601 or relative times")
    add(transactions, "--at", help="Activity on the UTC day containing this ISO-8601 or relative time")
    add(transactions, "--today", action="store_true", help="Activity during the current UTC day")
    add(transactions, "-T", "--local-time", action="store_true", help="Use CSMS receive time for event display, filtering, and ordering")
    add(transactions, "-n", "--limit", type=int, default=20, help="Maximum transactions to print (default: %(default)s)")
    add(transactions, "--events", action="store_true", help="Show OCPP timeline for a transaction ID")
    return transactions


def resolve_time(value: str, *, now: datetime | None = None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    match = _RELATIVE_TIME.fullmatch(value.strip())
    if match:
        return current - timedelta(seconds=float(match.group(1)) * _RELATIVE_SECONDS[match.group(2).upper()])
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid timestamp: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _day_bounds(value: datetime) -> tuple[datetime, datetime]:
    start = value.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1) - timedelta(microseconds=1)


def _time_filters(args: argparse.Namespace, *, now: datetime | None = None) -> tuple[datetime | None, datetime | None]:
    shortcuts = int(args.between is not None) + int(args.at is not None) + int(args.today)
    if shortcuts and (args.since or args.until):
        raise ValueError("--between/--at/--today cannot be combined with --since or --until")
    if shortcuts > 1:
        raise ValueError("--between, --at, and --today are mutually exclusive")
    current = now or datetime.now(timezone.utc)
    if args.today:
        return _day_bounds(current)
    if args.at is not None:
        return _day_bounds(resolve_time(args.at, now=current))
    if args.between is not None:
        since, until = (resolve_time(value, now=current) for value in args.between)
    else:
        since = resolve_time(args.since, now=current) if args.since else None
        until = resolve_time(args.until, now=current) if args.until else None
    if since is not None and until is not None and since > until:
        raise ValueError("lower time bound must not be after upper time bound")
    return since, until


def run_transactions(args: argparse.Namespace) -> str:
    if args.transaction_id is not None and args.transaction_id < 0:
        raise ValueError("transaction ID must be zero or greater")
    if args.connector is not None and args.connector < 0:
        raise ValueError("-c/--connector must be zero or greater")
    if args.limit < 1:
        raise ValueError("--limit must be at least 1")
    filtered = any((args.charger, args.connector is not None, args.id_tag, args.since, args.until, args.between, args.at, args.today))
    if args.events and args.transaction_id is None:
        raise ValueError("--events requires a transaction ID")
    if args.transaction_id is not None and (args.active or args.last or filtered or args.limit != 20):
        raise ValueError("transaction ID cannot be combined with list filters or selectors")

    query = TransactionQuery(args.data_dir)
    if args.transaction_id is not None:
        view = query.get(args.transaction_id)
        if view is None:
            return f"Transaction {args.transaction_id} not found."
        detail = format_transaction(view, local_time=args.local_time)
        if not args.events:
            return detail
        timeline = format_events(transaction_events(args.data_dir, args.transaction_id), heading=f"Transaction {args.transaction_id} OCPP events")
        return f"{detail}\n\n{timeline}"

    since, until = _time_filters(args)
    filters = {"charger": args.charger, "connector": args.connector, "id_tag": args.id_tag, "since": since, "until": until, "local_time": args.local_time}
    if args.active:
        views = query.active(**filters)
    elif args.last:
        view = query.last(**filters)
        views = [view] if view is not None else []
    else:
        views = query.list(limit=args.limit, **filters)
    return format_transactions(views, local_time=args.local_time)
