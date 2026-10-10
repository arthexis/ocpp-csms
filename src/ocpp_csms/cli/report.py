"""Operator-facing operational report command."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta

from ocpp_csms.cli.transactions import resolve_time
from ocpp_csms.output import emit_json
from ocpp_csms.reports import build_report, format_report


def add_report_command(subcommands):
    parser = subcommands.add_parser("report", help="Transaction, alert, and charger health report")
    parser.add_argument("--since", default="1d", help="Period start (default: 1d)")
    parser.add_argument("--until", help="Period end (default: now)")
    parser.add_argument("--cp", "--charger", dest="cp")
    parser.add_argument("-j", "--json", action="store_true")
    return parser


def report_for_args(args):
    now = datetime.now(timezone.utc)
    since = resolve_time(args.since, now=now)
    until = resolve_time(args.until, now=now) if args.until else now
    return build_report(args.data_dir, since=since, until=until, charger_id=args.cp)


def run_report(args):
    report = report_for_args(args)
    if args.json:
        emit_json(report)
    else:
        print(format_report(report))
    return 0
