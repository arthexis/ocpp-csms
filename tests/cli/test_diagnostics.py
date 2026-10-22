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


def test_events_filters_combine_with_transaction_cp_time_limit_and_formats(cli_parser, tmp_path, capsys):
    from ocpp_csms.cli.diagnostics import run_events
    store = EventStore(tmp_path)
    store.record_ocpp("A", "StartTransaction", {"transaction_id": 12, "connector_id": 1})
    store.record_ocpp("A", "MeterValues", {"transaction_id": 12, "connector_id": 1, "meter_value": []})
    store.record_ocpp("A", "StopTransaction", {"transaction_id": 12})
    store.record_ocpp("B", "StopTransaction", {"transaction_id": 12})
    store.record_ocpp("A", "Heartbeat", {})
    base = ["--data-dir", str(tmp_path), "events"]
    common = ["--cp", "A", "--tx", "12", "--only", "transaction,values", "--exclude", "values"]
    for extra in (["-n", "1"], ["-N"], ["--since", "30d"], ["--limit", "30d"],
                  ["--verbose"], ["--detail"], ["--json"], ["--json", "--raw"]):
        parsed = cli_parser.parse_args(base + common + extra)
        assert parsed.transaction == 12
        assert run_events(parsed) == 0
        output = capsys.readouterr().out
        assert "StopTransaction" in output
        assert "MeterValues" not in output
        assert "Heartbeat" not in output
        assert "StartTransaction" not in output or "-n" not in extra


def test_events_tx_aliases(cli_parser):
    for name in ("--tx", "--txn", "--transaction"):
        args = cli_parser.parse_args(["events", name, "42", "--exclude", "He"])
        assert args.transaction == 42
        assert args.exclude == "He"


def test_events_filters_with_follow_poll_and_cursor(tmp_path):
    from ocpp_csms.cli.event_follow import _new_events
    store = EventStore(tmp_path)
    cursor = {"ocpp": 0, "runtime": 0}
    store.record_ocpp("A", "Heartbeat", {})
    store.record_ocpp("A", "MeterValues", {"transaction_id": 8})
    store.record_ocpp("A", "StopTransaction", {"transaction_id": 8})
    rows = _new_events(tmp_path, cursor, charger_id="A", transaction_id=8,
                       only=("values", "transaction"), exclude=("values",))
    assert [row["action"] for row in rows] == ["StopTransaction"]
    assert cursor["ocpp"] == 3
    assert _new_events(tmp_path, cursor, only=("stop",)) == []
