"""A4b diagnose is read-only by default and requires --now for upload requests."""
import pytest

from ocpp_csms.cli import build_parser
from ocpp_csms.cli.charger_diagnostics import _request, run_diagnostics


def test_diagnose_history_is_default(monkeypatch, tmp_path, capsys):
    import ocpp_csms.cli.charger_diagnostics as module
    parser, commands = build_parser()
    assert "diagnose" in commands
    args = parser.parse_args(["--data-dir", str(tmp_path), "diagnose", "--cp", "CP1", "-n", "20", "-j"])
    monkeypatch.setattr(module, "events_between", lambda *a, **kw: [])
    assert run_diagnostics(args) == 0
    assert '"events": []' in capsys.readouterr().out


def test_diagnose_explicit_list():
    parser, _ = build_parser()
    args = parser.parse_args(["diagnose", "--list", "--cp", "CP1"])
    assert args.list and not args.now


def test_diagnose_now_request_contract():
    parser, _ = build_parser()
    args = parser.parse_args(["diagnose", "--now", "--cp", "CP1", "--location", "https://example.org/upload"])
    assert _request(args) == {"command": "get_diagnostics", "location": "https://example.org/upload", "charger": "CP1"}


def test_diagnose_now_requires_location():
    parser, _ = build_parser()
    args = parser.parse_args(["diagnose", "--now"])
    with pytest.raises(ValueError, match="--location is required"):
        _request(args)


def test_diagnose_rejects_both_modes():
    parser, _ = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["diagnose", "--now", "--list"])


@pytest.mark.parametrize("old", ["diag", "diagnostics"])
def test_old_commands_removed(old):
    parser, _ = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([old, "history"])
