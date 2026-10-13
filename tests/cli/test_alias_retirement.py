"""Canonical CLI control payload and selector contracts."""
from __future__ import annotations

import pytest

from ocpp_csms.cli import build_parser
from ocpp_csms.cli.control import control_request
from ocpp_csms.cli.transactions import build_txn_operations_parser


@pytest.mark.parametrize(("argv", "expected"), [
    (["start", "--cp", "CP1", "-c", "2", "--rfid", "TAG"],
     {"command": "start", "charger": "CP1", "connector": 2, "id_tag": "TAG", "timing": "now"}),
    (["stop", "42", "--after", "5"],
     {"command": "stop", "transaction": 42, "timing": "after", "seconds": 5}),
])
def test_canonical_control_payload_unchanged(argv, expected):
    args = build_txn_operations_parser().parse_args(argv)
    args.command = args.operation
    if args.command == "stop":
        args.transaction = args.transaction_id
        args.charger = None
    assert control_request(args) == expected


def test_reset_and_event_filters_remain_distinct():
    parser, _ = build_parser()
    assert parser.parse_args(["reset", "--cp", "CP1"]).charger_option == "CP1"
    assert parser.parse_args(["events", "--txn", "42"]).transaction == 42
