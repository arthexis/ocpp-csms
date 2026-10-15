from __future__ import annotations

import argparse
import re
from datetime import datetime, timedelta, timezone

from ocpp_csms.evidence.diagnostics import format_events, transaction_events
from ocpp_csms.cli.options import add_cp, add_connector, add_time_bounds, require_nonnegative, require_positive, validate_time_order
from ocpp_csms.transactions.formatting import format_transaction, format_transactions
from ocpp_csms.transactions.query import TransactionQuery

TRANSACTION_COMMANDS = ("txn",)
_RELATIVE_TIME = re.compile(r"^(\d+(?:\.\d+)?)([SMHDW])$", re.IGNORECASE)
_RELATIVE_SECONDS = {"S": 1, "M": 60, "H": 3600, "D": 86400, "W": 604800}


def add_transaction_parser(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> argparse.ArgumentParser:
    add = argparse.ArgumentParser.add_argument
    transactions = subcommands.add_parser("txn", help="Inspect archived transactions")
    transactions.set_defaults(command="txn")
    add(transactions, "transaction_id", nargs="?", type=int, help="Transaction ID for detailed inspection")
    selection = transactions.add_mutually_exclusive_group()
    selection.add_argument("--active", action="store_true", help="Show only active transactions")
    selection.add_argument("--last", action="store_true", help="Show the most recent transaction, including active")
    add_cp(transactions, help="Filter by charge point ID")
    add_connector(transactions, help="Filter by connector ID")
    add(transactions, "--rfid", dest="id_tag", help="Filter by OCPP idTag")
    add_time_bounds(transactions, since_help="Lower timestamp bound (ISO-8601 or relative, e.g. 7D)", until_help="Upper timestamp bound (ISO-8601 or relative, e.g. 2H)")
    add(transactions, "--between", nargs=2, metavar=("START", "END"), help="Activity between two ISO-8601 or relative times")
    add(transactions, "--at", help="Activity on the UTC day containing this ISO-8601 or relative time")
    add(transactions, "--today", action="store_true", help="Activity during the current UTC day")
    add(transactions, "-T", "--local-time", action="store_true", help="Use CSMS receive time for event display, filtering, and ordering")
    limits = transactions.add_mutually_exclusive_group()
    limits.add_argument("-n", "--limit", type=int, default=None, help="Maximum transactions to print (default: 20)")
    limits.add_argument("-N", "--no-limit", action="store_true", help="Show all matching transactions without the default 20-transaction cap")
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
    validate_time_order(since, until)
    return since, until


def run_transactions(args: argparse.Namespace) -> str:
    require_nonnegative(args.transaction_id, name="transaction ID")
    require_nonnegative(args.connector, name="-c/--connector")
    if args.limit is not None:
        require_positive(args.limit, name="--limit")
    filtered = any((args.charger, args.connector is not None, args.id_tag, args.since, args.until, args.between, args.at, args.today))
    if args.events and args.transaction_id is None:
        raise ValueError("--events requires a transaction ID")
    if args.transaction_id is not None and (args.active or args.last or filtered or (args.limit is not None or getattr(args, "no_limit", False))):
        raise ValueError("transaction ID cannot be combined with list filters or selectors")

    views = select_transaction_views(args)
    if args.transaction_id is not None:
        if not views:
            return f"Transaction {args.transaction_id} not found."
        detail = format_transaction(views[0], local_time=args.local_time)
        if not args.events:
            return detail
        timeline = format_events(transaction_events(args.data_dir, args.transaction_id), heading=f"Transaction {args.transaction_id} OCPP events")
        return f"{detail}\n\n{timeline}"
    return format_transactions(views, local_time=args.local_time)


def select_transaction_views(args: argparse.Namespace) -> list:
    """Shared read-only selection for text and JSON transaction presentations."""
    query = TransactionQuery(args.data_dir)
    if args.transaction_id is not None:
        view = query.get(args.transaction_id)
        return [view] if view is not None else []
    since, until = _time_filters(args)
    filters = {"charger": args.charger, "connector": args.connector, "id_tag": args.id_tag, "since": since, "until": until, "local_time": args.local_time}
    if args.active:
        return query.active(**filters)
    if args.last:
        view = query.last(**filters)
        return [view] if view is not None else []
    return query.list(limit=None if getattr(args, "no_limit", False) else (20 if args.limit is None else args.limit), **filters)


def build_txn_operations_parser() -> argparse.ArgumentParser:
    """Register transaction operations in one place, independent of dispatch.

    The query grammar retains 'txn [ID]' and therefore cannot use argparse's
    normal subparser positional slot simultaneously. The entry point selects
    this parser only when the token following 'txn' names an operation.
    """
    from ocpp_csms.cli.control import _add_timing_options

    parser = argparse.ArgumentParser(prog="ocpp-csms txn", description="Transaction operations")
    operations = parser.add_subparsers(dest="operation", required=True)
    start = operations.add_parser("start", help="Request remote transaction start")
    start.add_argument("charger", nargs="?")
    start.add_argument("--cp", dest="charger_option")
    start.add_argument("-c", "--connector", type=int)
    start.add_argument("--rfid", dest="id_tag")
    _add_timing_options(start)

    stop = operations.add_parser("stop", help="Request remote transaction stop")
    stop.add_argument("transaction_id", type=int)
    stop.add_argument("--cp", dest="charger_option")
    _add_timing_options(stop)

    explain = operations.add_parser("explain", help="Explain archived transaction evidence")
    explain.add_argument("transaction_id", type=int)
    explain.add_argument("--context", type=int, default=0)
    explain.add_argument("--verbose", action="store_true")
    explain.add_argument("-j", "--json", action="store_true")

    return parser
