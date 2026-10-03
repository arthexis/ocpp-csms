import asyncio

import pytest

from ocpp_csms.app import build_parser, run_transactions
from ocpp_csms.transactions import TransactionArchive


def parse(*argv: str):
    parser, _ = build_parser()
    return parser.parse_args(list(argv))


def listed_ids(text: str) -> list[int]:
    rows = text.splitlines()[1:]
    return [int(row.split()[0]) for row in rows if row.strip()]


def start_payload(*, charger_time: str, connector: int = 1, id_tag: str = "card-a"):
    return {
        "connector_id": connector,
        "id_tag": id_tag,
        "meter_start": 100,
        "timestamp": charger_time,
    }


def stop_payload(transaction_id: int, *, timestamp: str):
    return {
        "transaction_id": transaction_id,
        "meter_stop": 180,
        "timestamp": timestamp,
    }


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


def test_default_list_is_newest_first_and_limited_by_query(tmp_path):
    archive = TransactionArchive(tmp_path)
    first = asyncio.run(archive.start("charger-a", start_payload(charger_time="2026-10-02T10:00:00Z")))
    asyncio.run(archive.stop("charger-a", stop_payload(first, timestamp="2026-10-02T10:30:00Z")))
    second = asyncio.run(archive.start("charger-b", start_payload(charger_time="2026-10-02T11:00:00Z")))

    text = run_transactions(parse("--data-dir", str(tmp_path), "txn"))

    rows = text.splitlines()
    assert rows[0].startswith("TXN")
    assert listed_ids(text) == [second, first]
    assert "charger-b" in rows[1]


def test_active_and_last_never_return_same_transaction(tmp_path):
    archive = TransactionArchive(tmp_path)
    previous = asyncio.run(archive.start("charger-a", start_payload(charger_time="2026-10-02T10:00:00Z")))
    asyncio.run(archive.stop("charger-a", stop_payload(previous, timestamp="2026-10-02T10:30:00Z")))
    active = asyncio.run(archive.start("charger-a", start_payload(charger_time="2026-10-02T11:00:00Z")))

    active_text = run_transactions(parse("--data-dir", str(tmp_path), "txn", "--active"))
    last_text = run_transactions(parse("--data-dir", str(tmp_path), "txn", "--last"))

    assert listed_ids(active_text) == [active]
    assert listed_ids(last_text) == [previous]


def test_last_applies_filters_before_selection(tmp_path):
    archive = TransactionArchive(tmp_path)
    a = asyncio.run(archive.start("charger-a", start_payload(charger_time="2026-10-02T10:00:00Z", connector=1)))
    asyncio.run(archive.stop("charger-a", stop_payload(a, timestamp="2026-10-02T10:30:00Z")))
    b = asyncio.run(archive.start("charger-b", start_payload(charger_time="2026-10-02T11:00:00Z", connector=2)))
    asyncio.run(archive.stop("charger-b", stop_payload(b, timestamp="2026-10-02T11:30:00Z")))

    text = run_transactions(parse("--data-dir", str(tmp_path), "txn", "--charger", "charger-a", "--last"))

    assert listed_ids(text) == [a]
    assert "charger-a" in text


def test_transaction_id_shows_detail(tmp_path):
    archive = TransactionArchive(tmp_path)
    transaction_id = asyncio.run(
        archive.start(
            "charger-a",
            start_payload(charger_time="2026-10-02T10:00:00Z", connector=2, id_tag="rfid-7"),
        )
    )

    text = run_transactions(parse("--data-dir", str(tmp_path), "txn", str(transaction_id)))

    assert f"Transaction {transaction_id}" in text
    assert "Status:       open" in text
    assert "Charger:      charger-a" in text
    assert "Connector:    2" in text
    assert "RFID:         rfid-7" in text
    assert "Archive:      transactions/" in text
    assert str(tmp_path) not in text


def test_missing_transaction_has_clear_output(tmp_path):
    text = run_transactions(parse("--data-dir", str(tmp_path), "txn", "999"))

    assert text == "Transaction 999 not found."


def test_list_supports_id_tag_time_and_cp_filters(tmp_path):
    archive = TransactionArchive(tmp_path)
    old = asyncio.run(
        archive.start(
            "charger-a",
            start_payload(charger_time="2026-10-02T09:00:00Z", connector=1, id_tag="old-card"),
        )
    )
    asyncio.run(archive.stop("charger-a", stop_payload(old, timestamp="2026-10-02T09:15:00Z")))
    wanted = asyncio.run(
        archive.start(
            "charger-a",
            start_payload(charger_time="2026-10-02T11:00:00Z", connector=2, id_tag="wanted-card"),
        )
    )

    text = run_transactions(
        parse(
            "--data-dir",
            str(tmp_path),
            "txn",
            "--cp",
            "2",
            "--id-tag",
            "wanted-card",
            "--since",
            "2026-10-02T10:00:00Z",
        )
    )

    assert listed_ids(text) == [wanted]


def test_transaction_id_rejects_list_selectors(tmp_path):
    args = parse("--data-dir", str(tmp_path), "txn", "1", "--active")

    with pytest.raises(ValueError, match="cannot be combined"):
        run_transactions(args)


def test_invalid_limit_and_connector_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="limit"):
        run_transactions(parse("--data-dir", str(tmp_path), "txn", "--limit", "0"))

    with pytest.raises(ValueError, match="connector"):
        run_transactions(parse("--data-dir", str(tmp_path), "txn", "--cp", "-1"))
