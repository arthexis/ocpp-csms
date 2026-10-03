import asyncio

import pytest

from ocpp_csms.app import build_parser, run_transactions
from ocpp_csms.events import EventStore
from ocpp_csms.transactions import TransactionArchive


def parse(tmp_path, *argv: str):
    parser, _ = build_parser()
    return parser.parse_args(["--data-dir", str(tmp_path), *argv])


def test_detail_summarizes_duration_energy_and_meter_values(tmp_path):
    archive = TransactionArchive(tmp_path)
    transaction_id = asyncio.run(
        archive.start(
            "charger-a",
            {
                "connector_id": 1,
                "id_tag": "card-a",
                "meter_start": 1000,
                "timestamp": "2026-10-03T10:00:00Z",
            },
        )
    )
    asyncio.run(
        archive.meter_values(
            "charger-a",
            {
                "connector_id": 1,
                "transaction_id": transaction_id,
                "meter_value": [
                    {
                        "timestamp": "2026-10-03T10:05:00Z",
                        "sampled_value": [
                            {
                                "value": "1250",
                                "measurand": "Energy.Active.Import.Register",
                                "unit": "Wh",
                            },
                            {
                                "value": "6900",
                                "measurand": "Power.Active.Import",
                                "unit": "W",
                            },
                        ],
                    }
                ],
            },
        )
    )
    asyncio.run(
        archive.stop(
            "charger-a",
            {
                "transaction_id": transaction_id,
                "meter_stop": 1600,
                "timestamp": "2026-10-03T10:10:30Z",
            },
        )
    )

    text = run_transactions(parse(tmp_path, "txn", str(transaction_id)))

    assert "Duration:     10m 30s" in text
    assert "Meter start:  1000" in text
    assert "Meter stop:   1600" in text
    assert "Energy:       600 Wh" in text
    assert "messages:   1" in text
    assert "samples:    2" in text
    assert "first:      2026-10-03T10:05:00Z" in text
    assert "last:       2026-10-03T10:05:00Z" in text
    assert "latest energy: 1250 Wh" in text
    assert "latest power: 6900 W" in text


def test_detail_does_not_label_ambiguous_sample_values(tmp_path):
    archive = TransactionArchive(tmp_path)
    transaction_id = asyncio.run(
        archive.start(
            "charger-a",
            {
                "connector_id": 1,
                "id_tag": "card-a",
                "meter_start": 1000,
                "timestamp": "2026-10-03T10:00:00Z",
            },
        )
    )
    asyncio.run(
        archive.meter_values(
            "charger-a",
            {
                "connector_id": 1,
                "transaction_id": transaction_id,
                "meter_value": [
                    {
                        "timestamp": "2026-10-03T10:05:00Z",
                        "sampled_value": [{"value": "1250"}],
                    }
                ],
            },
        )
    )

    text = run_transactions(parse(tmp_path, "txn", str(transaction_id)))

    assert "samples:    1" in text
    assert "latest energy:" not in text
    assert "latest power:" not in text


def test_recovered_detail_explains_missing_start(tmp_path):
    archive = TransactionArchive(tmp_path)
    asyncio.run(
        archive.stop(
            "charger-a",
            {
                "transaction_id": 225,
                "meter_stop": 1800,
                "timestamp": "2026-10-03T10:10:00Z",
            },
        )
    )

    text = run_transactions(parse(tmp_path, "txn", "225"))

    assert "Origin:       recovered" in text
    assert "Recovery:" in text
    assert "start:      unknown" in text
    assert "recovered:  StopTransaction" in text


def test_detail_lists_collision_reason(tmp_path):
    archive = TransactionArchive(tmp_path)
    transaction_id = asyncio.run(
        archive.start(
            "charger-a",
            {
                "connector_id": 1,
                "id_tag": "card-a",
                "meter_start": 100,
                "timestamp": "2026-10-03T10:00:00Z",
            },
        )
    )
    asyncio.run(
        archive.stop(
            "charger-a",
            {
                "transaction_id": transaction_id,
                "meter_stop": 50,
                "timestamp": "2026-10-03T09:00:00Z",
            },
        )
    )

    text = run_transactions(parse(tmp_path, "txn", str(transaction_id)))

    assert "Warnings:" in text
    assert "unresolved StopTransaction: message_predates_local_start" in text


def test_events_requires_transaction_id(tmp_path):
    with pytest.raises(ValueError, match="requires a transaction ID"):
        run_transactions(parse(tmp_path, "txn", "--events"))


def test_events_appends_transaction_scoped_ocpp_timeline(tmp_path):
    archive = TransactionArchive(tmp_path)
    store = EventStore(tmp_path)
    transaction_id = asyncio.run(
        archive.start(
            "charger-a",
            {
                "connector_id": 1,
                "id_tag": "card-a",
                "meter_start": 100,
                "timestamp": "2026-10-03T10:00:00Z",
            },
        )
    )
    store.record_ocpp(
        "charger-a",
        "StartTransaction",
        {"connector_id": 1, "id_tag": "card-a", "meter_start": 100},
        transaction_id=transaction_id,
    )
    store.record_ocpp(
        "charger-a",
        "StartTransaction",
        {"idTagInfo": {"status": "Accepted"}, "transaction_id": transaction_id},
        direction="out",
        transaction_id=transaction_id,
    )
    store.record_ocpp("charger-a", "Heartbeat", {}, transaction_id=None)

    text = run_transactions(parse(tmp_path, "txn", str(transaction_id), "--events"))

    assert f"Transaction {transaction_id} OCPP events" in text
    assert "StartTransaction RFID card-a" in text
    assert "→ StartTransaction Accepted" in text
    assert "Heartbeat" not in text
