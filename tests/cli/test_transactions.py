from datetime import datetime, timezone

import pytest

from ocpp_csms.cli import build_parser
from ocpp_csms.cli.transactions import _time_filters, resolve_time, run_transactions


def parse(*argv: str):
    parser, _ = build_parser()
    return parser.parse_args(list(argv))


@pytest.mark.parametrize("name", ["transactions", "transaction", "txns", "txn"])
def test_transaction_aliases_normalize_to_transactions_command(name):
    args = parse(name, "--active")
    assert args.command == "transactions"
    assert args.active is True


def test_cp_alias_matches_connector_filter():
    assert parse("txn", "--cp", "2").connector == 2
    assert parse("transactions", "--connector", "2").connector == 2


def test_active_and_last_are_mutually_exclusive():
    parser, _ = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["txn", "--active", "--last"])


def test_events_requires_transaction_id(tmp_path):
    args = parse("--data-dir", str(tmp_path), "txn", "--events")
    with pytest.raises(ValueError):
        run_transactions(args)


def test_transaction_id_rejects_list_selectors(tmp_path):
    args = parse("--data-dir", str(tmp_path), "txn", "1", "--active")
    with pytest.raises(ValueError):
        run_transactions(args)


def test_invalid_limit_and_connector_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        run_transactions(parse("--data-dir", str(tmp_path), "txn", "--limit", "0"))
    with pytest.raises(ValueError):
        run_transactions(parse("--data-dir", str(tmp_path), "txn", "--cp", "-1"))


@pytest.mark.parametrize(
    ("value", "expected"),
    [("30S", 30), ("5m", 300), ("2H", 7200), ("7D", 604800), ("2w", 1209600)],
)
def test_relative_times_are_case_insensitive(value, expected):
    now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    assert (now - resolve_time(value, now=now)).total_seconds() == expected


def test_between_accepts_relative_and_iso_bounds():
    now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    args = parse("txn", "--between", "7D", "2026-10-06T11:00:00Z")
    since, until = _time_filters(args, now=now)
    assert since == datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
    assert until == datetime(2026, 10, 6, 11, 0, tzinfo=timezone.utc)


def test_at_selects_utc_calendar_day():
    now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    since, until = _time_filters(parse("transaction", "--at", "1D"), now=now)
    assert since == datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc)
    assert until.date().isoformat() == "2026-10-05"
    assert until.hour == 23 and until.minute == 59


def test_today_selects_current_utc_calendar_day():
    now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    since, until = _time_filters(parse("txns", "--today"), now=now)
    assert since == datetime(2026, 10, 6, 0, 0, tzinfo=timezone.utc)
    assert until.date().isoformat() == "2026-10-06"


def test_date_shortcuts_reject_conflicts_and_reversed_bounds():
    with pytest.raises(ValueError):
        _time_filters(parse("txn", "--today", "--since", "7D"))
    with pytest.raises(ValueError):
        _time_filters(parse("txn", "--between", "2026-10-06T12:00:00Z", "2026-10-05T12:00:00Z"))
