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


def test_parser_accepts_rfid_report_with_optional_tag():
    parser, _ = build_parser()

    summary = parser.parse_args(["rfid", "report"])
    detailed = parser.parse_args(["rfid", "report", "card-a"])

    assert summary.command == "rfid"
    assert summary.rfid_command == "report"
    assert summary.tag is None
    assert detailed.tag == "card-a"


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


@pytest.mark.asyncio
async def test_report_without_tag_summarizes_observed_rfids(tmp_path):
    archive = TransactionArchive(tmp_path)
    a1 = await archive.start(
        "charger-a",
        start_payload(id_tag="card-a", meter_start=1000, timestamp="2026-10-06T10:00:00Z"),
    )
    await archive.stop("charger-a", stop_payload(a1, meter_stop=2500))
    a2 = await archive.start(
        "charger-a",
        start_payload(id_tag="card-a", meter_start=3000, timestamp="2026-10-06T11:00:00Z"),
    )
    await archive.stop("charger-a", stop_payload(a2, meter_stop=5500, timestamp="2026-10-06T11:30:00Z"))
    b1 = await archive.start(
        "charger-a",
        start_payload(id_tag="card-b", meter_start=7000, timestamp="2026-10-06T12:00:00Z"),
    )
    await archive.stop("charger-a", stop_payload(b1, meter_stop=7750, timestamp="2026-10-06T12:30:00Z"))

    report = run_rfid(args(tmp_path, None))

    assert "RFID" in report
    assert "TXNS" in report
    assert "ENERGY" in report
    assert "card-a" in report
    assert "2" in report
    assert "4.000 kWh" in report
    assert "card-b" in report
    assert "750 Wh" in report
    assert "ALLOW" not in report
    assert "NAME" not in report


@pytest.mark.asyncio
async def test_summary_adds_allow_and_name_only_when_authorization_file_exists(tmp_path):
    (tmp_path / "rfid.csv").write_text(
        "rfid,name,enabled\ncard-a,Alice,true\ncard-b,Former,false\n",
        encoding="utf-8",
    )
    archive = TransactionArchive(tmp_path)
    for index, tag in enumerate(("card-a", "card-b", "card-c"), start=1):
        transaction_id = await archive.start(
            "charger-a",
            start_payload(
                id_tag=tag,
                meter_start=index * 1000,
                timestamp=f"2026-10-06T{9 + index:02d}:00:00Z",
            ),
        )
        await archive.stop(
            "charger-a",
            stop_payload(
                transaction_id,
                meter_stop=index * 1000 + 500,
                timestamp=f"2026-10-06T{9 + index:02d}:30:00Z",
            ),
        )

    report = run_rfid(args(tmp_path, None))
    header = report.splitlines()[0]

    assert header.index("ENERGY") < header.index("ALLOW") < header.index("NAME")
    rows = {line.split()[0]: line for line in report.splitlines()[1:]}
    assert "true" in rows["card-a"]
    assert "Alice" in rows["card-a"]
    assert "false" in rows["card-b"]
    assert "Former" in rows["card-b"]
    assert "missing" in rows["card-c"]


@pytest.mark.asyncio
async def test_summary_marks_incomplete_energy_per_rfid(tmp_path):
    archive = TransactionArchive(tmp_path)
    complete = await archive.start("charger-a", start_payload(id_tag="card-a", meter_start=1000))
    await archive.stop("charger-a", stop_payload(complete, meter_stop=2500))
    await archive.start(
        "charger-a",
        start_payload(id_tag="card-a", meter_start=3000, timestamp="2026-10-06T11:00:00Z"),
    )

    report = run_rfid(args(tmp_path, None))

    assert "1.500 kWh (1/2)" in report


def test_summary_with_no_captured_rfids_is_empty(tmp_path):
    assert run_rfid(args(tmp_path, None)) == "No RFID transactions."
