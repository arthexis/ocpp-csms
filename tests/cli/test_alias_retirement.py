"""Removed CLI spellings fail fast; canonical operations retain their contracts."""
from __future__ import annotations

import sys

import pytest

from ocpp_csms.cli import build_parser, main
from ocpp_csms.cli.control import control_request
from ocpp_csms.cli.transactions import build_txn_operations_parser


@pytest.mark.parametrize("argv", [
    ["start"], ["stop"], ["recover", "--dry-run"],
    ["reset", "--charger", "CP1"],
    ["txn", "--charger", "CP1"],
    ["txn", "--id-tag", "TAG"],
    ["config", "--charger", "CP1"],
    ["txn", "start", "--id-tag", "TAG"],
    ["txn", "stop", "--txn", "42"],
    ["txn", "stop", "--transaction", "42"],
    ["txn", "stop", "-t", "42"],
])
def test_retired_spellings_fail_usage(monkeypatch, tmp_path, argv):
    monkeypatch.setattr(sys, "argv", ["ocpp-csms", "--data-dir", str(tmp_path), *argv])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2


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
