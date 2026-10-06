from __future__ import annotations

import argparse

from ocpp_csms.diagnostics import format_events, transaction_events
from ocpp_csms.transaction_cli import format_transaction, format_transactions
from ocpp_csms.transaction_query import TransactionQuery


def add_transaction_parser(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    """Register the transaction inspection command and its existing alias."""
    add = argparse.ArgumentParser.add_argument
    transactions = subcommands.add_parser(
        "transactions",
        aliases=["txn"],
        help="Inspect archived transactions",
    )
    transactions.set_defaults(command="transactions")
    add(transactions, "transaction_id", nargs="?", type=int, help="Transaction ID for detailed inspection")
    selection = transactions.add_mutually_exclusive_group()
    selection.add_argument("--active", action="store_true", help="Show only active transactions")
    selection.add_argument("--last", action="store_true", help="Show the most recent non-active transaction")
    add(transactions, "--charger", help="Filter by charge point ID")
    add(transactions, "--connector", "--cp", dest="connector", type=int, help="Filter by connector ID")
    add(transactions, "--id-tag", help="Filter by OCPP idTag")
    add(transactions, "--since", help="ISO-8601 lower timestamp bound")
    add(transactions, "--until", help="ISO-8601 upper timestamp bound")
    add(transactions, "--limit", type=int, default=20, help="Maximum transactions to print (default: %(default)s)")
    add(transactions, "--events", action="store_true", help="Show OCPP timeline for a transaction ID")
    return transactions


def run_transactions(args: argparse.Namespace) -> str:
    """Inspect transaction archives using the parsed transaction command arguments."""
    if args.transaction_id is not None and args.transaction_id < 0:
        raise ValueError("transaction ID must be zero or greater")
    if args.connector is not None and args.connector < 0:
        raise ValueError("--connector/--cp must be zero or greater")
    if args.limit < 1:
        raise ValueError("--limit must be at least 1")
    filtered = any((args.charger, args.connector is not None, args.id_tag, args.since, args.until))
    if args.events and args.transaction_id is None:
        raise ValueError("--events requires a transaction ID")
    if args.transaction_id is not None and (args.active or args.last or filtered or args.limit != 20):
        raise ValueError("transaction ID cannot be combined with list filters or selectors")

    query = TransactionQuery(args.data_dir)
    if args.transaction_id is not None:
        view = query.get(args.transaction_id)
        if view is None:
            return f"Transaction {args.transaction_id} not found."
        detail = format_transaction(view)
        if not args.events:
            return detail
        timeline = format_events(
            transaction_events(args.data_dir, args.transaction_id),
            heading=f"Transaction {args.transaction_id} OCPP events",
        )
        return f"{detail}\n\n{timeline}"

    filters = {
        "charger": args.charger,
        "connector": args.connector,
        "id_tag": args.id_tag,
        "since": args.since,
        "until": args.until,
    }
    if args.active:
        return format_transactions(query.active(**filters))
    if args.last:
        view = query.last(**filters)
        return format_transactions([view] if view is not None else [])
    return format_transactions(query.list(limit=args.limit, **filters))
