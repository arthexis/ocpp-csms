"""A4d D2: lock CLI parser/dispatch contracts without executing charger actions."""
from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from ocpp_csms.cli import build_parser, main


@pytest.mark.parametrize(("argv", "expected"), [
    (["status"], "status"),
    (["cps"], "cps"),
    (["txn"], "txn"),
    (["txn", "-j"], "txn"),
    (["reserve"], "reserve"),
    (["diagnose"], "diagnose"),
    (["inspect"], "inspect"),
    (["capabilities"], "capabilities"),
    (["reconcile"], "reconcile"),
    (["export"], "export"),
])
def test_canonical_root_commands_parse(argv, expected):
    parser, commands = build_parser()
    assert parser.parse_args(argv).command == expected
    assert expected in commands


@pytest.mark.parametrize("argv", [
    ["diag"], ["diagnostics"], ["reservation"], ["transaction"],
])
def test_removed_root_commands_fail_closed(argv):
    parser, commands = build_parser()
    assert argv[0] not in commands
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(argv)
    assert exc.value.code == 2


@pytest.mark.parametrize(("argv", "expected"), [
    (["--data-dir", "/tmp/csms-contract", "status"], "/tmp/csms-contract"),
    (["status"], None),
])
def test_data_dir_global_scope(argv, expected):
    parser, _ = build_parser()
    parsed = parser.parse_args(argv)
    if expected is not None:
        assert parsed.data_dir == expected
    else:
        assert parsed.data_dir


def test_transaction_json_flag_is_explicit():
    parser, _ = build_parser()
    assert parser.parse_args(["txn", "-j"]).json is True
    assert parser.parse_args(["txn"]).json is False


def test_help_dispatch_is_read_only(capsys):
    with patch.object(sys, "argv", ["ocpp-csms", "help"]):
        assert main() == 0
    assert "usage:" in capsys.readouterr().out


def test_removed_command_exit_code(capsys):
    with patch.object(sys, "argv", ["ocpp-csms", "diag"]):
        with pytest.raises(SystemExit) as exc:
            main()
    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err
