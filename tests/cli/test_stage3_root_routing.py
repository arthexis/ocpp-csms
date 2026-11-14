"""Hermetic Stage 3 CLI routing contracts; no sockets, services or database writes."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import ocpp_csms.cli as cli


@pytest.mark.parametrize(("command_name", "handler_name"), [
    ("init", "run_appliance"),
    ("start", "run_control"),
    ("trigger", "run_maintenance"),
    ("avail", "run_maintenance"),
    ("availability", "run_maintenance"),
    ("inspect", "run_inspect"),
    ("capabilities", "run_inspection"),
    ("reconcile", "run_inspection"),
    ("cps", "run_chargers"),
    ("reserve", "run_reservation"),
    ("data", "run_data"),
    ("data-transfer", "run_data_transfer"),
    ("firmware", "run_firmware"),
    ("diagnose", "run_diagnostics"),
    ("tls", "run_tls"),
    ("auth", "run_auth"),
    ("report", "run_report"),
    ("rating", "run_rating"),
    ("profile", "run_profile"),
    ("energy", "run_energy"),
    ("export", "run_export"),
    ("events", "run_diagnostic"),
    ("status", "run_diagnostic"),
])
def test_root_route_uses_monkeypatchable_exports(monkeypatch, command_name, handler_name):
    calls = []
    monkeypatch.setattr(cli, handler_name, lambda parsed: calls.append(parsed.command) or 27)
    assert cli._dispatch_parsed_command(SimpleNamespace(command=command_name), {}) == 27
    assert calls == [command_name]


def test_route_table_binds_handlers_each_invocation(monkeypatch):
    parsed = SimpleNamespace(command="cps")
    monkeypatch.setattr(cli, "run_chargers", lambda args: 17)
    assert cli._dispatch_parsed_command(parsed, {}) == 17
    monkeypatch.setattr(cli, "run_chargers", lambda args: 29)
    assert cli._dispatch_parsed_command(parsed, {}) == 29


def test_root_route_preserves_earlier_family_precedence(monkeypatch):
    monkeypatch.setattr(cli, "APPLIANCE_COMMANDS", ("collision",))
    monkeypatch.setattr(cli, "CONTROL_COMMANDS", ("collision",))
    monkeypatch.setattr(cli, "run_appliance", lambda args: 41)
    monkeypatch.setattr(cli, "run_control", lambda args: pytest.fail("lower priority dispatched"))
    assert cli._dispatch_parsed_command(SimpleNamespace(command="collision"), {}) == 41


@pytest.mark.parametrize(("diff_match", "download_match", "expected_value", "expected_calls"), [
    (True, True, 11, ["is_diff", "diff"]),
    (False, True, 12, ["is_diff", "is_download", "download"]),
    (False, False, 13, ["is_diff", "is_download", "configure"]),
])
def test_config_diff_download_fallback_precedence(
    monkeypatch, diff_match, download_match, expected_value, expected_calls
):
    calls = []
    monkeypatch.setattr(cli, "is_config_diff", lambda args: calls.append("is_diff") or diff_match)
    monkeypatch.setattr(cli, "is_config_download", lambda args: calls.append("is_download") or download_match)
    monkeypatch.setattr(cli, "run_config_diff", lambda args: calls.append("diff") or 11)
    monkeypatch.setattr(cli, "run_config_download", lambda args: calls.append("download") or 12)
    monkeypatch.setattr(cli, "run_configuration", lambda args: calls.append("configure") or 13)
    assert cli._dispatch_parsed_command(SimpleNamespace(command="config"), {}) == expected_value
    assert calls == expected_calls


@pytest.mark.parametrize(("subcommand", "expected_value", "expected_output"), [
    (None, 0, "RFID usage\n"),
    ("edit", 21, ""),
    ("report", 0, "RFID report\n"),
    ("export", 23, ""),
])
def test_rfid_root_routing_preserves_help_report_and_exit_codes(
    monkeypatch, capsys, subcommand, expected_value, expected_output
):
    calls = []
    monkeypatch.setattr(cli, "run_rfid_edit", lambda args: calls.append("edit") or 21)
    monkeypatch.setattr(cli, "run_rfid", lambda args: calls.append("report") or "RFID report")
    monkeypatch.setattr(cli, "run_rfid_action", lambda args: calls.append("action") or 23)
    parser = SimpleNamespace(print_help=lambda: print("RFID usage"))
    parsed = SimpleNamespace(command="rfid", rfid_command=subcommand)
    assert cli._dispatch_parsed_command(parsed, {"rfid": parser}) == expected_value
    assert capsys.readouterr().out == expected_output
    assert calls == ([] if subcommand is None else
                     ["edit"] if subcommand == "edit" else
                     ["report"] if subcommand == "report" else ["action"])


@pytest.mark.parametrize("json_mode", [False, True])
def test_txn_query_keeps_text_json_and_return_code(monkeypatch, capsys, json_mode):
    seen = []
    monkeypatch.setattr(cli, "run_transactions", lambda args: seen.append("text") or "txn text")
    monkeypatch.setattr(cli, "run_transactions_json", lambda args: seen.append("json") or 31)
    result = cli._dispatch_parsed_command(SimpleNamespace(command="txn", json=json_mode), {})
    assert result == (31 if json_mode else 0)
    assert capsys.readouterr().out == ("" if json_mode else "txn text\n")
    assert seen == (["json"] if json_mode else ["text"])


def test_transaction_soc_preserves_json_emitter_and_exact_parameters(monkeypatch):
    calls = []
    payload = {"soc": 40, "source": "recorded"}
    monkeypatch.setattr(
        cli, "transaction_soc",
        lambda data_dir, *, cp, transaction: calls.append((data_dir, cp, transaction)) or payload,
    )
    monkeypatch.setattr(cli, "emit_json", lambda value: calls.append(("json", value)))
    parsed = SimpleNamespace(command="transaction-soc", data_dir="/unused", cp="CP-1", transaction=42)
    assert cli._dispatch_parsed_command(parsed, {}) == 0
    assert calls == [("/unused", "CP-1", 42), ("json", payload)]


def test_unknown_root_command_returns_previous_fallback():
    assert cli._dispatch_parsed_command(SimpleNamespace(command="unknown"), {}) == 2


def test_main_catches_value_error_as_argparse_usage_error(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "cli_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["ocpp-csms", "status"])
    def fail_status(args):
        raise ValueError("synthetic invalid status")
    monkeypatch.setattr(cli, "run_diagnostic", fail_status)
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert "synthetic invalid status" in capsys.readouterr().err


@pytest.mark.parametrize(("argv", "expected_code"), [
    (["--help"], 0),
    (["txn", "stop", "--help"], 0),
    (["reserve", "--help"], 0),
    (["txn", "stop"], 2),
    (["reserve", "-1"], 2),
])
def test_main_keeps_legacy_argparse_help_and_error_codes(monkeypatch, tmp_path, capsys, argv, expected_code):
    monkeypatch.setattr(cli, "cli_data_dir", lambda: str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["ocpp-csms", *argv])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == expected_code
    captured = capsys.readouterr()
    assert "usage:" in (captured.out if expected_code == 0 else captured.err)
