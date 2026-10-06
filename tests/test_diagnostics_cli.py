from ocpp_csms.cli.diagnostics import run_diagnostic
from ocpp_csms.events import EventStore


def test_status_runs_through_cli_module(cli_parser, tmp_path, capsys):
    args = cli_parser.parse_args(["--data-dir", str(tmp_path), "status"])

    assert run_diagnostic(args) == 0
    assert "CSMS:" in capsys.readouterr().out


def test_events_runs_through_cli_module(cli_parser, tmp_path, capsys):
    store = EventStore(tmp_path)
    store.record_ocpp("charger-a", "Heartbeat", {})
    args = cli_parser.parse_args(["--data-dir", str(tmp_path), "events", "charger-a"])

    assert run_diagnostic(args) == 0
    assert "Heartbeat" in capsys.readouterr().out


def test_explain_runs_through_cli_module(cli_parser, tmp_path, capsys):
    store = EventStore(tmp_path)
    store.record_ocpp("charger-a", "Heartbeat", {})
    occurred_at = store.connection.execute(
        "SELECT received_at FROM events ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
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
    assert "charger-a around" in output
    assert "Heartbeat" in output
