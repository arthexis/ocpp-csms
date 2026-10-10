"""Check txn control shortcuts use existing control parser and timing defaults."""
import sys

import pytest

from ocpp_csms.cli import build_parser, main
from ocpp_csms.cli.control import control_request


@pytest.mark.parametrize(("argv", "expected"), [
    (["start"], {"command": "start", "timing": "now", "id_tag": "AUTO"}),
    (["start", "-c", "2", "--rfid", "ABC"], {"command": "start", "timing": "now", "id_tag": "ABC", "connector": 2}),
    (["stop", "--txn", "42"], {"command": "stop", "timing": "now", "transaction": 42}),
    (["stop", "--txn", "42", "--after", "10"], {"command": "stop", "timing": "after", "seconds": 10, "transaction": 42}),
])
def test_control_request_defaults_to_now(argv, expected):
    parser, _ = build_parser()
    assert control_request(parser.parse_args(argv)) == expected


@pytest.mark.parametrize(("shortcut", "canonical"), [
    (["txn", "start", "-c", "1"], ["start", "-c", "1"]),
    (["txn", "stop", "42"], ["stop", "--txn", "42"]),
    (["txn", "stop", "--txn", "42", "--within", "5"], ["stop", "--txn", "42", "--within", "5"]),
    (["transactions", "start"], ["start"]),
])
def test_alias_dispatch(monkeypatch, shortcut, canonical):
    seen = []
    monkeypatch.setattr("ocpp_csms.cli.run_control", lambda args: seen.append(control_request(args)) or 0)
    monkeypatch.setattr(sys, "argv", ["ocpp-csms", *shortcut])
    assert main() == 0
    parser, _ = build_parser()
    assert seen == [control_request(parser.parse_args(canonical))]


def test_txn_query_still_works(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ocpp-csms", "txn", "42"])
    monkeypatch.setattr("ocpp_csms.cli.run_transactions", lambda args: str(args.transaction_id))
    assert main() == 0
