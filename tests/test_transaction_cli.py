import pytest

from ocpp_csms.app import build_parser
from ocpp_csms.cli.transactions import run_transactions


def parse(*argv: str):
    parser, _ = build_parser()
    return parser.parse_args(list(argv))


def test_txn_alias_normalizes_to_transactions_command():
    args = parse("txn", "--active")

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
