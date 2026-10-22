from ocpp_csms.cli.diagnostics import run_diagnostic
from ocpp_csms.evidence.diagnostics import events_between
from ocpp_csms.evidence.store import EventStore


def test_status_runs_through_cli_module(cli_parser, tmp_path, capsys):
    args = cli_parser.parse_args(["--data-dir", str(tmp_path), "status"])

    assert run_diagnostic(args) == 0
    assert "CSMS:" in capsys.readouterr().out


def test_events_runs_through_cli_module(cli_parser, tmp_path, capsys):
    store = EventStore(tmp_path)
    store.record_ocpp("charger-a", "Heartbeat", {})
    args = cli_parser.parse_args(["--data-dir", str(tmp_path), "events", "--cp", "charger-a"])

    assert run_diagnostic(args) == 0
    assert "Heartbeat" in capsys.readouterr().out


def test_explain_runs_through_cli_module(cli_parser, tmp_path, capsys):
    store = EventStore(tmp_path)
    store.record_ocpp("charger-a", "Heartbeat", {})
    occurred_at = events_between(tmp_path, charger_id="charger-a")[0]["occurred_at"]
    args = cli_parser.parse_args([
        "--data-dir",
        str(tmp_path),
        "explain",
        "--cp", "charger-a",
        "--at",
        occurred_at,
    ])

    assert run_diagnostic(args) == 0
    output = capsys.readouterr().out
    assert "charger-a around" in output
    assert "Heartbeat" in output


def test_events_action_filters_with_partial_case_insensitive_names(cli_parser, tmp_path, capsys):
    store = EventStore(tmp_path)
    store.record_ocpp("charger-a", "StartTransaction", {"connector_id": 1})
    store.record_ocpp("charger-a", "Heartbeat", {})
    store.record_ocpp("charger-a", "MeterValues", {"connector_id": 1, "meter_value": []})
    store.record_ocpp("charger-a", "StatusNotification", {"connector_id": 1, "status": "Available"})
    base = ["--data-dir", str(tmp_path), "events"]

    assert run_diagnostic(cli_parser.parse_args(base + ["--exclude", "beAt,vaL", "-n", "2"])) == 0
    output = capsys.readouterr().out
    assert "StartTransaction" in output
    assert "StatusNotification" in output
    assert "Heartbeat" not in output
    assert "MeterValues" not in output

    assert run_diagnostic(cli_parser.parse_args(base + ["--only", "HE,vaL"])) == 0
    output = capsys.readouterr().out
    assert "Heartbeat" in output
    assert "MeterValues" in output
    assert "StartTransaction" not in output

    assert run_diagnostic(cli_parser.parse_args(base + ["--only", "notification", "--exclude", "status"])) == 0
    assert "No matching events." in capsys.readouterr().out


def test_events_action_filters_reject_empty_fragments(cli_parser, tmp_path):
    import pytest
    from ocpp_csms.cli.diagnostics import parse_action_patterns
    with pytest.raises(ValueError):
        parse_action_patterns("He,,Me")
    with pytest.raises(ValueError):
        parse_action_patterns(" ")
