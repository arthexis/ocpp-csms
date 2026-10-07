import argparse

import pytest

from ocpp_csms.cli import build_parser
import ocpp_csms.cli.rfid as rfid_module
from ocpp_csms.cli.rfid import run_rfid, run_rfid_action
from ocpp_csms.rfid_cache import RFIDCacheState
from ocpp_csms.rfid_list_query import RFIDListEntrySnapshot, RFIDListSnapshot
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


def test_parser_accepts_rfid_local_list_commands():
    parser, _ = build_parser()

    export = parser.parse_args(["rfid", "export", "charger-a"])
    version = parser.parse_args(["rfid", "version"])
    clear = parser.parse_args(["rfid", "clear", "--charger", "charger-a"])

    assert export.rfid_command == "export"
    assert export.charger == "charger-a"
    assert version.rfid_command == "version"
    assert version.charger is None
    assert clear.rfid_command == "clear"
    assert clear.charger_option == "charger-a"


def test_export_requires_valid_rfid_file(tmp_path):
    parsed = build_parser()[0].parse_args(
        ["--data-dir", str(tmp_path), "rfid", "export"]
    )

    with pytest.raises(ValueError, match="rfid.csv is required"):
        rfid_module._control_request(parsed)

    (tmp_path / "rfid.csv").write_text("rfid,enabled\nCARD-A,maybe\n", encoding="utf-8")
    with pytest.raises(ValueError, match="rfid.csv is invalid"):
        rfid_module._control_request(parsed)


def test_export_sends_only_enabled_cards(monkeypatch, tmp_path, capsys):
    (tmp_path / "rfid.csv").write_text(
        "rfid,name,enabled\nCARD-A,Alice,true\nCARD-B,Former,false\n",
        encoding="utf-8",
    )
    parsed = build_parser()[0].parse_args(
        ["--data-dir", str(tmp_path), "rfid", "export", "charger-a"]
    )
    captured = {}

    async def fake_send(data_dir, request):
        captured.update(request)
        return {
            "ok": True,
            "response": {
                "status": "Accepted",
                "charger": "charger-a",
                "previous_version": 2,
                "list_version": 3,
                "cards": 1,
                "verified_version": 3,
            },
        }

    monkeypatch.setattr(rfid_module, "send_control", fake_send)

    assert run_rfid_action(parsed) == 0
    assert captured["command"] == "rfid_export"
    assert captured["charger"] == "charger-a"
    assert captured["source_file"] == "rfid.csv"
    assert captured["entries"] == [
        {"rfid": "CARD-A", "name": "Alice", "enabled": True}
    ]
    assert isinstance(captured["list_hash"], str)
    assert len(captured["list_hash"]) == 64
    output = capsys.readouterr().out
    assert "Cards:            1" in output
    assert "Verified:         3" in output


def test_export_returns_failure_when_charger_rejects(monkeypatch, tmp_path):
    (tmp_path / "rfid.csv").write_text("CARD-A\n", encoding="utf-8")
    parsed = build_parser()[0].parse_args(
        ["--data-dir", str(tmp_path), "rfid", "export"]
    )

    async def fake_send(data_dir, request):
        return {
            "ok": True,
            "response": {
                "status": "Failed",
                "charger": "charger-a",
                "previous_version": 2,
                "list_version": 3,
                "cards": 1,
                "verified_version": None,
            },
        }

    monkeypatch.setattr(rfid_module, "send_control", fake_send)

    assert run_rfid_action(parsed) == 1


def test_version_and_clear_use_control_socket(monkeypatch, tmp_path, capsys):
    parser, _ = build_parser()
    requests = []

    async def fake_send(data_dir, request):
        requests.append(request)
        if request["command"] == "rfid_version":
            return {"ok": True, "response": {"list_version": 7}}
        return {
            "ok": True,
            "response": {
                "status": "Accepted",
                "charger": "charger-a",
                "previous_version": 7,
                "list_version": 0,
                "cards": 0,
                "verified_version": 0,
            },
        }

    monkeypatch.setattr(rfid_module, "send_control", fake_send)

    version = parser.parse_args(["--data-dir", str(tmp_path), "rfid", "version"])
    clear = parser.parse_args(["--data-dir", str(tmp_path), "rfid", "clear"])

    assert run_rfid_action(version) == 0
    assert "RFID local list version: 7" in capsys.readouterr().out
    assert run_rfid_action(clear) == 0
    assert requests == [{"command": "rfid_version"}, {"command": "rfid_clear"}]


def cache_state(*, version=7, entries=(), known=True, has_history=True):
    snapshot = None
    if known:
        snapshot = RFIDListSnapshot(
            id=1,
            charger_id="charger-a",
            list_version=version,
            sent_at="2026-10-07T04:00:00Z",
            source_file="rfid.csv",
            list_hash="hash",
            verified_version=version,
            entries=tuple(entries),
        )
    return RFIDCacheState(
        charger_id="charger-a",
        list_version=version,
        has_history=has_history,
        snapshot=snapshot,
    )


@pytest.mark.asyncio
async def test_summary_uses_known_cache_as_allow_source_when_no_rfid_file(monkeypatch, tmp_path):
    archive = TransactionArchive(tmp_path)
    transaction_id = await archive.start(
        "charger-a",
        start_payload(id_tag="card-a", meter_start=1000),
    )
    await archive.stop("charger-a", stop_payload(transaction_id, meter_stop=2000))
    other = await archive.start(
        "charger-a",
        start_payload(id_tag="card-b", meter_start=3000, timestamp="2026-10-06T11:00:00Z"),
    )
    await archive.stop("charger-a", stop_payload(other, meter_stop=3500))

    monkeypatch.setattr(
        rfid_module,
        "resolve_rfid_cache_sync",
        lambda data_dir: cache_state(
            entries=(
                RFIDListEntrySnapshot("card-a", "Alice", True),
                RFIDListEntrySnapshot("card-x", "Disabled", False),
            )
        ),
    )

    report = run_rfid(args(tmp_path, None))
    lines = report.splitlines()
    header = lines[0]
    rows = {line.split()[0]: line for line in lines[1:]}

    assert "ALLOW" in header
    assert "CACHE" not in header
    assert "NAME" in header
    assert "true" in rows["card-a"]
    assert "Alice" in rows["card-a"]
    assert "missing" in rows["card-b"]


@pytest.mark.asyncio
async def test_summary_shows_allow_and_cache_when_file_and_known_cache_both_exist(monkeypatch, tmp_path):
    (tmp_path / "rfid.csv").write_text(
        "rfid,name,enabled\ncard-a,Alice,true\ncard-b,Bob,false\ncard-c,Carol,true\n",
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

    monkeypatch.setattr(
        rfid_module,
        "resolve_rfid_cache_sync",
        lambda data_dir: cache_state(
            entries=(
                RFIDListEntrySnapshot("card-a", "Old Alice", True),
                RFIDListEntrySnapshot("card-b", "Old Bob", True),
            )
        ),
    )

    report = run_rfid(args(tmp_path, None))
    header = report.splitlines()[0]
    rows = {line.split()[0]: line for line in report.splitlines()[1:]}

    assert header.index("ENERGY") < header.index("ALLOW") < header.index("CACHE") < header.index("NAME")
    assert "true" in rows["card-a"]
    assert "Alice" in rows["card-a"]
    assert rows["card-b"].count("true") == 1
    assert "false" in rows["card-b"]
    assert "Bob" in rows["card-b"]
    assert "missing" in rows["card-c"]
    assert "Carol" in rows["card-c"]


@pytest.mark.asyncio
async def test_summary_shows_unknown_cache_when_connected_version_is_not_in_history(monkeypatch, tmp_path):
    (tmp_path / "rfid.csv").write_text("card-a,Alice,true\n", encoding="utf-8")
    archive = TransactionArchive(tmp_path)
    transaction_id = await archive.start("charger-a", start_payload(id_tag="card-a"))
    await archive.stop("charger-a", stop_payload(transaction_id, meter_stop=2000))

    monkeypatch.setattr(
        rfid_module,
        "resolve_rfid_cache_sync",
        lambda data_dir: cache_state(version=9, known=False, has_history=True),
    )

    report = run_rfid(args(tmp_path, None))

    assert "CACHE" in report.splitlines()[0]
    assert "unknown" in report.splitlines()[1]


@pytest.mark.asyncio
async def test_summary_omits_cache_when_charger_is_disconnected(monkeypatch, tmp_path):
    (tmp_path / "rfid.csv").write_text("card-a,Alice,true\n", encoding="utf-8")
    archive = TransactionArchive(tmp_path)
    transaction_id = await archive.start("charger-a", start_payload(id_tag="card-a"))
    await archive.stop("charger-a", stop_payload(transaction_id, meter_stop=2000))

    monkeypatch.setattr(rfid_module, "resolve_rfid_cache_sync", lambda data_dir: None)

    report = run_rfid(args(tmp_path, None))
    header = report.splitlines()[0]

    assert "ALLOW" in header
    assert "CACHE" not in header


@pytest.mark.asyncio
async def test_summary_ignores_connected_charger_cache_when_we_have_no_history(monkeypatch, tmp_path):
    archive = TransactionArchive(tmp_path)
    transaction_id = await archive.start("charger-a", start_payload(id_tag="card-a"))
    await archive.stop("charger-a", stop_payload(transaction_id, meter_stop=2000))

    monkeypatch.setattr(
        rfid_module,
        "resolve_rfid_cache_sync",
        lambda data_dir: cache_state(version=3, known=False, has_history=False),
    )

    report = run_rfid(args(tmp_path, None))
    header = report.splitlines()[0]

    assert header.split() == ["RFID", "TXNS", "ENERGY"]
