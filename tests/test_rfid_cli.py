import argparse

import pytest

from ocpp_csms.cli import build_parser
from ocpp_csms.cli.rfid import run_rfid
from ocpp_csms.transactions import TransactionArchive


def start_payload(*, id_tag="card-a", meter_start=1000, timestamp="2026-10-06T10:00:00Z"):
    return {
        "connector_id": 1,
        "id_tag": id_tag,
        "meter_start": meter_start,
        "timestamp": timestamp,
    }


def stop_payload(transaction_id, *, meter_stop, timestamp="2026-10-06T10:30:00Z"):
    return {
        "transaction_id": transaction_id,
        "meter_stop": meter_stop,
        "timestamp": timestamp,
    }


def args(tmp_path, tag="card-a"):
    return argparse.Namespace(data_dir=str(tmp_path), rfid_command="report", tag=tag)


def test_parser_accepts_rfid_report_tag():
    parser, _ = build_parser()

    parsed = parser.parse_args(["rfid", "report", "card-a"])

    assert parsed.command == "rfid"
    assert parsed.rfid_command == "report"
    assert parsed.tag == "card-a"


@pytest.mark.asyncio
async def test_report_lists_transactions_and_sums_complete_energy(tmp_path):
    archive = TransactionArchive(tmp_path)
    first = await archive.start("charger-a", start_payload(meter_start=1000, timestamp="2026-10-06T10:00:00Z"))
    await archive.stop("charger-a", stop_payload(first, meter_stop=2500, timestamp="2026-10-06T10:30:00Z"))
    second = await archive.start("charger-a", start_payload(meter_start=3000, timestamp="2026-10-06T11:00:00Z"))
    await archive.stop("charger-a", stop_payload(second, meter_stop=5500, timestamp="2026-10-06T11:30:00Z"))

    report = run_rfid(args(tmp_path))

    assert "RFID card-a" in report
    assert str(first) in report
    assert str(second) in report
    assert "Transactions: 2" in report
    assert "Energy:       4.000 kWh" in report
    assert "of 2 transactions" not in report


@pytest.mark.asyncio
async def test_report_qualifies_energy_only_when_transaction_energy_is_missing(tmp_path):
    archive = TransactionArchive(tmp_path)
    complete = await archive.start("charger-a", start_payload(meter_start=1000))
    await archive.stop("charger-a", stop_payload(complete, meter_stop=2500))
    await archive.start("charger-a", start_payload(meter_start=3000, timestamp="2026-10-06T11:00:00Z"))

    report = run_rfid(args(tmp_path))

    assert "Transactions: 2" in report
    assert "Energy:       1.500 kWh (1 of 2 transactions)" in report


def test_report_for_unseen_rfid_is_empty(tmp_path):
    report = run_rfid(args(tmp_path, "unknown-card"))

    assert report == "RFID unknown-card\n\nNo transactions.\n\nTransactions: 0\nEnergy:       0 Wh"
