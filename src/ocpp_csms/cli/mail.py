"""Manual mail operator commands. Automatic delivery is a later chunk."""
from __future__ import annotations

import argparse
import smtplib
import ssl
from pathlib import Path

from ocpp_csms.mail import load_mail_config, mail_status, send_test
from ocpp_csms.notifications import history, policy, OUTBOX
from ocpp_csms.output import emit_json


def add_mail_command(subcommands):
    parser = subcommands.add_parser("mail", help="Inspect or test SMTP transport")
    parser.add_argument("--config", default="/etc/ocpp-csms/mail.toml", help="Mail TOML configuration path")
    children = parser.add_subparsers(dest="mail_command", required=True)
    status = children.add_parser("status", help="Show safe mail configuration status")
    status.add_argument("-j", "--json", action="store_true")
    test = children.add_parser("test", help="Send one explicit SMTP test message")
    history_parser = children.add_parser("history", help="Inspect automatic message delivery history")
    history_parser.add_argument("--failed", action="store_true")
    history_parser.add_argument("-j", "--json", action="store_true")
    return parser


def run_mail(args: argparse.Namespace) -> int:
    try:
        config = load_mail_config(Path(args.config))
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid mail configuration: {exc}") from None
    if args.mail_command == "status":
        status = mail_status(config)
        db_path = Path(args.data_dir) / OUTBOX
        if db_path.exists():
            records = history(Path(args.data_dir))
            status["recent_deliveries"] = len(records)
            status["pending"] = sum(1 for item in records if item["state"] == "pending")
        if args.json:
            emit_json({"schema": "ocpp-csms/mail-status/v1", "data": status})
        else:
            for key, value in status.items():
                print(f"{key.replace('_', ' ').title()}: {value}")
        return 0
    if args.mail_command == "history":
        records = history(Path(args.data_dir), failed=args.failed)
        if args.json:
            emit_json({"schema": "ocpp-csms/mail-history/v1", "data": {"messages": records}})
        else:
            for item in records:
                print(f"{item['created_at']} {item['kind']} {item['state']} to={item['recipient']} attempts={item['attempts']}")
            if not records:
                print("No matching mail deliveries.")
        return 0
    if args.mail_command == "test":
        if config is None:
            raise ValueError("mail configuration not found")
        try:
            send_test(config)
        except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
            # Avoid echoing a possibly sensitive SMTP exception response.
            raise ValueError(f"SMTP test failed ({type(exc).__name__}); verify endpoint, TLS and credentials") from None
        print("SMTP server accepted the test message; recipient delivery is not guaranteed.")
        return 0
    raise ValueError("unknown mail operation")
