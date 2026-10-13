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
    (["txn", "start"], ["start"]),
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


@pytest.mark.parametrize(("argv", "expected"), [
    (["txn", "start", "-c", "2", "--rfid", "ABC"], {"command": "start", "timing": "now", "connector": 2, "id_tag": "ABC"}),
    (["txn", "stop", "42", "--after", "10"], {"command": "stop", "timing": "after", "seconds": 10, "transaction": 42}),
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


def test_txn_recover_subparser_dispatch(monkeypatch):
    seen = []
    monkeypatch.setattr("ocpp_csms.cli.run_recovery", lambda args: seen.append((args.dry_run, args.charger)) or 0)
    monkeypatch.setattr(sys, "argv", ["ocpp-csms", "txn", "recover", "--dry-run", "--cp", "CP1"])
    assert main() == 0
    assert seen == [(True, "CP1")]


@pytest.mark.parametrize("argv", [
    ["txn", "stop"],
    ["txn", "stop", "42", "--txn", "43"],
    ["txn", "stop", "-1"],
    ["txn", "start", "-c", "-1"],
    ["txn", "start", "CP1", "--cp", "CP2"],
    ["txn", "explain", "-1"],
    ["txn", "explain", "42", "--context", "1441"],
    ["txn", "recover", "--enable"],
])
def test_txn_operations_reject_invalid_requests(monkeypatch, argv, tmp_path):
    monkeypatch.setattr(sys, "argv", ["ocpp-csms", "--data-dir", str(tmp_path), *argv])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2


@pytest.mark.parametrize("argv", [
    ["txn", "start", "--help"],
    ["txn", "stop", "--help"],
    ["txn", "explain", "--help"],
    ["txn", "recover", "--help"],
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
    assert "txn recover" in commands["txn"].format_help()
    assert parser.parse_args(["txn", "42"]).transaction_id == 42
