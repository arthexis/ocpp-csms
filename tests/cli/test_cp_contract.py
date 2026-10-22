"""Canonical charger selection: --cp, never a positional charger identifier."""
import pytest
from ocpp_csms.cli import build_parser
from ocpp_csms.cli.transactions import build_txn_operations_parser


@pytest.mark.parametrize("command", ["status", "events", "alerts", "reset", "start", "faults"])
def test_cp_available_on_charger_commands(command):
    parser, _ = build_parser()
    args = parser.parse_args([command, "--cp", "CP001"])
    selected = getattr(args, "charger_option", None) or getattr(args, "charger", None)
    assert selected == "CP001"


@pytest.mark.parametrize("command", ["status", "events", "alerts", "reset", "start", "faults"])
def test_positional_charger_is_rejected(command):
    parser, _ = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([command, "CP001"])


def test_explain_requires_cp():
    parser, _ = build_parser()
    assert parser.parse_args(["explain", "--cp", "CP001"]).charger == "CP001"
    with pytest.raises(SystemExit):
        parser.parse_args(["explain", "CP001"])


def test_txn_start_uses_cp_only():
    parser = build_txn_operations_parser()
    assert parser.parse_args(["start", "--cp", "CP001"]).charger_option == "CP001"
    with pytest.raises(SystemExit):
        parser.parse_args(["start", "CP001"])
