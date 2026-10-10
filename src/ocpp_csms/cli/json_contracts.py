from __future__ import annotations

import argparse

from ocpp_csms.cli.transactions import _time_filters
from ocpp_csms.output import emit_json
from ocpp_csms.transactions.contracts import transactions_contract
from ocpp_csms.transactions.query import TransactionQuery


def run_transactions_json(args: argparse.Namespace) -> int:
    if args.events:
        raise ValueError("--events is not available with --json")
    if args.transaction_id is not None and args.transaction_id < 0:
        raise ValueError("transaction ID must be zero or greater")
    if args.connector is not None and args.connector < 0:
        raise ValueError("--connector/--c must be zero or greater")
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit must be at least 1")
    filtered = any((args.charger, args.connector is not None, args.id_tag, args.since, args.until, args.between, args.at, args.today))
    if args.transaction_id is not None and (args.active or args.last or filtered or (args.limit is not None or getattr(args, "no_limit", False))):
        raise ValueError("transaction ID cannot be combined with list filters or selectors")

    query = TransactionQuery(args.data_dir)
    if args.transaction_id is not None:
        view = query.get(args.transaction_id)
        views = [view] if view is not None else []
    else:
        since, until = _time_filters(args)
        filters = {
            "charger": args.charger,
            "connector": args.connector,
            "id_tag": args.id_tag,
            "since": since,
            "until": until,
            "local_time": args.local_time,
        }
        if args.active:
            views = query.active(**filters)
        elif args.last:
            view = query.last(**filters)
            views = [view] if view is not None else []
        else:
            views = query.list(limit=None if getattr(args, "no_limit", False) else (20 if args.limit is None else args.limit), **filters)
    emit_json(transactions_contract(views, local_time=args.local_time))
    return 0
