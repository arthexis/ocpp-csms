from datetime import datetime, timezone
import json

import pytest

from ocpp_csms.cli.transactions import _time_filters, resolve_time, run_transactions


def test_canonical_txn_command(parse_cli):
    args = parse_cli("txn", "--active")
    assert args.command == "txn"
    assert args.active is True


def test_c_alias_matches_connector_filter(parse_cli):
    assert parse_cli("txn", "-c", "2").connector == 2
    assert parse_cli("txn", "--connector", "2").connector == 2


def test_local_time_long_and_short_flags_match(parse_cli):
    assert parse_cli("txn", "-T").receive_time is True
    assert parse_cli("txn", "--receive-time").receive_time is True
    assert parse_cli("txn", "-L").local_time is True
    assert parse_cli("txn", "-LT").local_time is True
    assert parse_cli("txn", "-LT").receive_time is True
    assert parse_cli("txn", "--local-time").local_time is True


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
    since, until = _time_filters(parse_cli("txn", "--at", "1D"), now=now)
    assert since == datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc)
    assert until.date().isoformat() == "2026-10-05"
    assert until.hour == 23 and until.minute == 59


def test_today_selects_current_utc_calendar_day(parse_cli):
    now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    since, until = _time_filters(parse_cli("txn", "--today"), now=now)
    assert since == datetime(2026, 10, 6, 0, 0, tzinfo=timezone.utc)
    assert until.date().isoformat() == "2026-10-06"


def test_date_shortcuts_reject_conflicts_and_reversed_bounds(parse_cli):
    with pytest.raises(ValueError):
        _time_filters(parse_cli("txn", "--today", "--since", "7D"))
    with pytest.raises(ValueError):
        _time_filters(parse_cli("txn", "--between", "2026-10-06T12:00:00Z", "2026-10-05T12:00:00Z"))


def test_local_time_changes_event_time_display_and_time_filter(parse_cli, tmp_path):
    from ocpp_csms.evidence.store import EventStore
    from ocpp_csms.schema import database_path
    import sqlite3

    store = EventStore(tmp_path)
    transaction_id = store.commit_start("charger-a", {
        "connector_id": 1, "id_tag": "CARD-A", "meter_start": 100,
        "timestamp": "2020-01-01T00:00:00Z",
    })
    store.commit_transaction_evidence("charger-a", "StopTransaction", {
        "transaction_id": transaction_id, "meter_stop": 150,
        "timestamp": "2020-01-01T00:10:00Z",
    })
    with sqlite3.connect(database_path(tmp_path)) as db:
        db.execute(
            "UPDATE transactions SET start_received_at = ?, stop_received_at = ?, last_activity_at = ? WHERE transaction_id = ?",
            ("2026-10-06T12:00:00Z", "2026-10-06T12:10:00Z", "2026-10-06T12:10:00Z", transaction_id),
        )


    default_output = run_transactions(parse_cli("--data-dir", str(tmp_path), "txn"))
    local_output = run_transactions(parse_cli("--data-dir", str(tmp_path), "txn", "-T", "--since", "2026-10-06T12:05:00Z"))
    assert "EVENT TIME" in default_output
    assert "2020-01-01T00:10Z" in default_output
    assert "2026-10-06T12:10Z" in local_output


def test_cp_and_charger_select_charge_point(parse_cli):
    assert parse_cli("txn", "--cp", "charger-a").charger == "charger-a"
    assert parse_cli("txn", "--cp", "charger-a").charger == "charger-a"


@pytest.mark.parametrize("command", ["txn"])
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


def test_txn_table_minute_energy_and_soc_formatting(tmp_path):
    from ocpp_csms.transactions.formatting import format_transactions
    from ocpp_csms.transactions.query import TransactionView

    def sample(value):
        return {"measurand": "SoC", "unit": "Percent", "value": str(value)}

    view = TransactionView(record={
        "transaction_id": 10, "status": "open", "charge_point_id": "cp-a",
        "connector_id": 1, "id_tag": "RFID", "updated_at": "2026-10-23T14:35:27.123456Z",
        "start": {"timestamp": "2026-10-23T14:00:00Z", "meter_start": 1000},
        "stop": None,
        "meter_values": [
            {"meter_value": [{"timestamp": "2026-10-23T14:01:00Z", "sampled_value": [sample(41)]}]},
            {"meter_value": [{"timestamp": "2026-10-23T14:35:27Z", "sampled_value": [
                sample(80), {"measurand": "Energy.Active.Import.Register", "value": "5500", "unit": "Wh"}
            ]}]},
        ],
    }, path=tmp_path / "csms.sqlite3")
    output = format_transactions([view])
    assert "KWH" in output and "SoC %" in output
    assert "ENERGY" not in output and "kWh" not in output
    assert "4.50" in output
    assert "41 80" in output
    assert "2026-10-23T14:35Z" in output
    assert "14:35:27" not in output


def test_txn_soc_missing_and_single_sample(tmp_path):
    from ocpp_csms.transactions.formatting import format_transactions
    from ocpp_csms.transactions.query import TransactionView

    base = {
        "transaction_id": 1, "status": "open", "charge_point_id": "cp",
        "start": {"timestamp": "2026-10-23T01:00:00Z", "meter_start": 10},
        "stop": None,
    }
    missing = TransactionView(record={**base, "meter_values": []}, path=tmp_path)
    one = TransactionView(record={**base, "meter_values": [
        {"meter_value": [{"sampled_value": [
            {"measurand": "SoC", "value": "62", "unit": "Percent"},
            {"measurand": "SoC", "value": "999", "unit": "Percent"},
        ]}]}
    ]}, path=tmp_path)
    rows = format_transactions([missing, one]).splitlines()
    assert "- -" in rows[1]
    assert "- 62" in rows[2]


def test_transaction_time_flags_are_independent(parse_cli, monkeypatch, tmp_path):
    import os
    import time
    from ocpp_csms.transactions.formatting import format_transactions
    from ocpp_csms.transactions.query import TransactionView

    if not hasattr(time, "tzset"):
        pytest.skip("requires POSIX timezone support")
    monkeypatch.setenv("TZ", "Etc/GMT+6")
    time.tzset()
    try:
        view = TransactionView(record={
            "transaction_id": 1, "status": "open", "charge_point_id": "CP",
            "start": {"timestamp": "2026-10-23T12:00:00Z"},
            "updated_at": "2026-10-23T18:35:45Z",
        }, path=tmp_path)
        assert "2026-10-23T12:00Z" in format_transactions([view])
        assert "2026-10-23T06:00-06:00" in format_transactions([view], display_local=True)
        assert "2026-10-23T18:35Z" in format_transactions([view], local_time=True)
        assert "2026-10-23T12:35-06:00" in format_transactions(
            [view], local_time=True, display_local=True
        )
        flags = parse_cli("txn", "-LT")
        assert flags.local_time and flags.receive_time
    finally:
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()
