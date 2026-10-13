"""A4b diagnostics short command and legacy compatibility."""
import pytest

from ocpp_csms.cli import build_parser


@pytest.mark.parametrize("name", ["diag", "diagnostics"])
def test_diagnostics_aliases_parse_request(name):
    parser, commands = build_parser()
    args = parser.parse_args([name, "request", "--cp", "CP1", "--location", "https://example.org/upload"])
    assert args.command == name
    assert args.diagnostics_action == "request"
    assert args.charger == "CP1"
    assert commands["diag"] is commands["diagnostics"]


@pytest.mark.parametrize("name", ["diag", "diagnostics"])
def test_diagnostics_aliases_parse_history(name):
    parser, _ = build_parser()
    args = parser.parse_args([name, "history", "--cp", "CP1", "-n", "20", "-j"])
    assert args.diagnostics_action == "history"
    assert args.limit == 20
    assert args.json
