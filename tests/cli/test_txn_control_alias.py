"""Canonical transaction operations preserve control payloads and safety."""
import sys

import pytest

from ocpp_csms.cli import build_parser, main
from ocpp_csms.cli.control import control_request


def test_txn_query_still_works(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ocpp-csms", "txn", "42"])
    monkeypatch.setattr("ocpp_csms.cli.run_transactions", lambda args: str(args.transaction_id))
    assert main() == 0


@pytest.mark.parametrize(("argv", "expected"), [
    (["txn", "start", "-c", "2", "--rfid", "ABC"], {"command": "start", "connector": 2, "id_tag": "ABC"}),
    (["txn", "stop", "42"], {"command": "stop", "transaction": 42}),
])
def test_txn_operation_subparser_dispatch(monkeypatch, argv, expected):
    seen = []
    monkeypatch.setattr("ocpp_csms.cli.run_control", lambda args: seen.append(control_request(args)) or 0)
    monkeypatch.setattr(sys, "argv", ["ocpp-csms", *argv])
    assert main() == 0
    assert seen == [expected]


def test_txn_explain_subparser_dispatch(monkeypatch):
    seen = []
    monkeypatch.setattr("ocpp_csms.cli.run_txn_explain", lambda args: seen.append((args.transaction_id, args.context)) or 0)
    monkeypatch.setattr(sys, "argv", ["ocpp-csms", "txn", "explain", "42", "--context", "5"])
    assert main() == 0
    assert seen == [(42, 5)]


@pytest.mark.parametrize("argv", [
    ["txn", "stop"],
    ["txn", "stop", "-1"],
    ["txn", "start", "-c", "-1"],
    ["txn", "start", "CP1", "--cp", "CP2"],
    ["txn", "explain", "-1"],
    ["txn", "explain", "42", "--context", "1441"],
])
def test_txn_operations_reject_invalid_inputs(monkeypatch, argv, tmp_path):
    monkeypatch.setattr(sys, "argv", ["ocpp-csms", "--data-dir", str(tmp_path), *argv])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2


@pytest.mark.parametrize("argv", [
    ["txn", "start", "--help"],
    ["txn", "stop", "--help"],
    ["txn", "explain", "--help"],
])
def test_txn_operations_have_help(monkeypatch, argv, capsys):
    monkeypatch.setattr(sys, "argv", ["ocpp-csms", *argv])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    assert "usage:" in capsys.readouterr().out


def test_transaction_parser_registration_and_help():
    from ocpp_csms.cli.transactions import build_txn_operations_parser
    operations = build_txn_operations_parser()
    assert operations.parse_args(["explain", "42"]).operation == "explain"
    assert operations.parse_args(["stop", "42"]).transaction_id == 42
    parser, commands = build_parser()
    assert "txn explain" in commands["txn"].format_help()
    assert parser.parse_args(["txn", "42"]).transaction_id == 42
