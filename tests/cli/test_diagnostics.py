from ocpp_csms.cli.diagnostics import run_diagnostic
from ocpp_csms.diagnostics import events_between
from ocpp_csms.events import EventStore


def test_status_runs_through_cli_module(cli_parser, tmp_path, capsys):
    args = cli_parser.parse_args(["--data-dir", str(tmp_path), "status"])

    assert run_diagnostic(args) == 0
    assert capsys.readouterr().out.strip()


def test_events_runs_through_cli_module(cli_parser, tmp_path, capsys):
    store = EventStore(tmp_path)
    store.record_ocpp("charger-a", "Heartbeat", {})
    args = cli_parser.parse_args(["--data-dir", str(tmp_path), "events", "charger-a"])

    assert run_diagnostic(args) == 0
    assert capsys.readouterr().out.strip()


def test_explain_runs_through_cli_module(cli_parser, tmp_path, capsys):
    store = EventStore(tmp_path)
    store.record_ocpp("charger-a", "Heartbeat", {})
    occurred_at = events_between(tmp_path, charger_id="charger-a")[0]["occurred_at"]
    args = cli_parser.parse_args([
        "--data-dir",
        str(tmp_path),
        "explain",
        "charger-a",
        "--at",
        occurred_at,
    ])

    assert run_diagnostic(args) == 0
    output = capsys.readouterr().out
    assert output.strip()
