from datetime import datetime, timezone
import json

import pytest

from ocpp_csms.cli.transactions import _time_filters, resolve_time, run_transactions


@pytest.mark.parametrize("name", ["transactions", "transaction", "txns", "txn"])
def test_transaction_aliases_normalize_to_transactions_command(parse_cli, name):
    args = parse_cli(name, "--active")
    assert args.command == "transactions"
    assert args.active is True


def test_c_alias_matches_connector_filter(parse_cli):
    assert parse_cli("txn", "-c", "2").connector == 2
    assert parse_cli("transactions", "--connector", "2").connector == 2


def test_local_time_long_and_short_flags_match(parse_cli):
    assert parse_cli("txn", "-T").local_time is True
    assert parse_cli("transactions", "--local-time").local_time is True


def test_active_and_last_are_mutually_exclusive(cli_parser):
    with pytest.raises(SystemExit):
        cli_parser.parse_args(["txn", "--active", "--last"])


def test_events_requires_transaction_id(parse_cli, tmp_path):
    with pytest.raises(ValueError):
        run_transactions(parse_cli("--data-dir", str(tmp_path), "txn", "--events"))


def test_transaction_id_rejects_list_selectors(parse_cli, tmp_path):
    with pytest.raises(ValueError):
        run_transactions(parse_cli("--data-dir", str(tmp_path), "txn", "1", "--active"))


def test_invalid_limit_and_connector_are_rejected(parse_cli, tmp_path):
    with pytest.raises(ValueError):
        run_transactions(parse_cli("--data-dir", str(tmp_path), "txn", "--limit", "0"))
    with pytest.raises(ValueError):
        run_transactions(parse_cli("--data-dir", str(tmp_path), "txn", "-c", "-1"))


@pytest.mark.parametrize(("value", "expected"), [("30S", 30), ("5m", 300), ("2H", 7200), ("7D", 604800), ("2w", 1209600)])
def test_relative_times_are_case_insensitive(value, expected):
    now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    assert (now - resolve_time(value, now=now)).total_seconds() == expected


def test_between_accepts_relative_and_iso_bounds(parse_cli):
    now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    since, until = _time_filters(parse_cli("txn", "--between", "7D", "2026-10-06T11:00:00Z"), now=now)
    assert since == datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
    assert until == datetime(2026, 10, 6, 11, 0, tzinfo=timezone.utc)


def test_at_selects_utc_calendar_day(parse_cli):
    now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    since, until = _time_filters(parse_cli("transaction", "--at", "1D"), now=now)
    assert since == datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc)
    assert until.date().isoformat() == "2026-10-05"
    assert until.hour == 23 and until.minute == 59


def test_today_selects_current_utc_calendar_day(parse_cli):
    now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    since, until = _time_filters(parse_cli("txns", "--today"), now=now)
    assert since == datetime(2026, 10, 6, 0, 0, tzinfo=timezone.utc)
    assert until.date().isoformat() == "2026-10-06"


def test_date_shortcuts_reject_conflicts_and_reversed_bounds(parse_cli):
    with pytest.raises(ValueError):
        _time_filters(parse_cli("txn", "--today", "--since", "7D"))
    with pytest.raises(ValueError):
        _time_filters(parse_cli("txn", "--between", "2026-10-06T12:00:00Z", "2026-10-05T12:00:00Z"))


def test_local_time_changes_event_time_display_and_time_filter(parse_cli, tmp_path):
    directory = tmp_path / "transactions" / "2026-10-06"
    directory.mkdir(parents=True)
    record = {
        "transaction_id": 1, "origin": "local", "charge_point_id": "charger-a", "status": "stopped",
        "created_at": "2026-10-06T12:00:00Z", "updated_at": "2026-10-06T12:10:00Z",
        "start": {"connector_id": 1, "timestamp": "2020-01-01T00:00:00Z"}, "start_received_at": "2026-10-06T12:00:00Z",
        "meter_values": [], "meter_values_received_at": [],
        "stop": {"transaction_id": 1, "timestamp": "2020-01-01T00:10:00Z"}, "stop_received_at": "2026-10-06T12:10:00Z",
    }
    (directory / "charger-a-1.json").write_text(json.dumps(record))

    default_output = run_transactions(parse_cli("--data-dir", str(tmp_path), "txn"))
    local_output = run_transactions(parse_cli("--data-dir", str(tmp_path), "txn", "-T", "--since", "2026-10-06T12:05:00Z"))
    assert "EVENT TIME" in default_output
    assert "2020-01-01T00:10:00Z" in default_output
    assert "2026-10-06T12:10:00Z" in local_output


def test_cp_and_charger_select_charge_point(parse_cli):
    assert parse_cli("txn", "--cp", "charger-a").charger == "charger-a"
    assert parse_cli("txn", "--charger", "charger-a").charger == "charger-a"


@pytest.mark.parametrize("command", ["txn", "txns", "transaction", "transactions"])
@pytest.mark.parametrize("flag", ["-N", "--no-limit"])
def test_transaction_no_limit_aliases(parse_cli, command, flag):
    args = parse_cli(command, "--since", "3d", flag)
    assert args.no_limit is True
    assert args.since == "3d"


@pytest.mark.parametrize("limit_flag, unlimited_flag", [
    ("--limit", "--no-limit"), ("-n", "-N"),
])
def test_transaction_limit_and_no_limit_are_mutually_exclusive(limit_flag, unlimited_flag):
    from ocpp_csms.cli import build_parser

    parser, _ = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["txn", limit_flag, "20", unlimited_flag])


def test_unlimited_transaction_query_forwards_none(parse_cli, monkeypatch):
    from ocpp_csms.cli import transactions as cli

    observed = []
    monkeypatch.setattr(cli.TransactionQuery, "list", lambda self, **kwargs: observed.append(kwargs) or [])
    cli.run_transactions(parse_cli("txn", "--since", "3d", "-N"))
    assert len(observed) == 1
    assert observed[0]["limit"] is None
    assert observed[0]["since"] is not None


def test_transaction_id_rejects_no_limit(parse_cli, tmp_path):
    with pytest.raises(ValueError):
        run_transactions(parse_cli("--data-dir", str(tmp_path), "txn", "1", "-N"))
