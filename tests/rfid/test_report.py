import pytest

from ocpp_csms.transactions.archive import default_data_dir

import ocpp_csms.cli.rfid as rfid_module
from ocpp_csms.cli import build_parser
from ocpp_csms.cli.rfid import run_rfid, run_rfid_edit
from ocpp_csms.rfid.list_query import RFIDListEntrySnapshot
from ocpp_csms.transactions.archive import TransactionArchive
from tests.rfid.helpers import cache_state, report_args as args, start_payload, stop_payload


def test_parser_accepts_rfid_report_with_optional_tag():
    parser, _ = build_parser()

    summary = parser.parse_args(["rfid", "report"])
    detailed = parser.parse_args(["rfid", "report", "card-a"])

    assert summary.command == "rfid"
    assert summary.rfid_command == "report"
    assert summary.tag is None
    assert detailed.tag == "card-a"


def test_rfid_help_shows_authorization_file_location_and_fields():
    parser, commands = build_parser()
    help_text = commands["rfid"].format_help()

    assert str(default_data_dir() / "rfid.csv") in help_text
    assert "<data-dir>/rfid.csv" in help_text
    assert "rfid,name,enabled" in help_text
    assert "all RFID tags are accepted" in help_text
    assert parser.parse_args(["rfid"]).rfid_command is None


def test_rfid_edit_respects_editor_and_data_dir(monkeypatch, tmp_path):
    commands = []

    def fake_run(argv, *, check):
        commands.append((argv, check))
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setenv("VISUAL", "code --wait")
    monkeypatch.setenv("EDITOR", "vim")
    monkeypatch.setattr(rfid_module.subprocess, "run", fake_run)
    parsed = build_parser()[0].parse_args(["--data-dir", str(tmp_path), "rfid", "edit"])

    assert run_rfid_edit(parsed) == 0
    assert commands == [(["code", "--wait", str(tmp_path / "rfid.csv")], False)]
    assert not (tmp_path / "rfid.csv").exists()


def test_rfid_edit_falls_back_to_nano_and_propagates_failure(monkeypatch, tmp_path):
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    captured = []

    def fake_run(argv, *, check):
        captured.append(argv)
        return type("Result", (), {"returncode": 3})()

    monkeypatch.setattr(rfid_module.subprocess, "run", fake_run)
    parsed = build_parser()[0].parse_args(["--data-dir", str(tmp_path), "rfid", "edit"])

    assert run_rfid_edit(parsed) == 3
    assert captured == [["nano", str(tmp_path / "rfid.csv")]]


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
    assert "LAST SEEN" in report.splitlines()[0]
    assert "2026-10-06 11:30" in report
    assert "2026-10-06 12:30" in report
    assert "card-a" in report
    assert "2" in report
    assert "4.000 kWh" in report
    assert "card-b" in report
    assert "750 Wh" in report
    assert "CURRENT AUTH" in report.splitlines()[0]
    assert "LABEL" in report.splitlines()[0]
    assert "Accepted" in report


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

    assert header.index("ENERGY") < header.index("CURRENT AUTH") < header.index("LABEL")
    rows = {line.split()[0]: line for line in report.splitlines()[1:] if line.strip() and not line.startswith(("Charger local list:", "Sync:"))}
    assert "Accepted" in rows["card-a"]
    assert "Alice" in rows["card-a"]
    assert "Blocked" in rows["card-b"]
    assert "Former" in rows["card-b"]
    assert "Unregistered" in rows["card-c"]
    assert rows["card-c"].endswith("--")


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


@pytest.mark.asyncio
async def test_summary_last_seen_uses_latest_activity_even_when_transaction_open(tmp_path):
    archive = TransactionArchive(tmp_path)
    first = await archive.start(
        "charger-a", start_payload(id_tag="card-a", timestamp="2026-10-06T09:00:00Z"),
    )
    await archive.stop(
        "charger-a", stop_payload(first, meter_stop=2000, timestamp="2026-10-06T09:30:00Z"),
    )
    await archive.start(
        "charger-a", start_payload(id_tag="card-a", timestamp="2026-10-06T13:00:00Z"),
    )

    report = run_rfid(args(tmp_path, None))
    row = next(line for line in report.splitlines() if line.startswith("card-a"))
    assert "2026-10-06 13:00" in row
    assert "2026-10-06 09:30" not in row


def test_summary_with_no_captured_rfids_is_empty(tmp_path):
    assert run_rfid(args(tmp_path, None)) == "No RFID transactions."


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
    rows = {line.split()[0]: line for line in lines[1:] if line.strip() and not line.startswith(("Charger local list:", "Sync:"))}

    assert "CURRENT AUTH" in header
    assert "CACHE" not in header
    assert "LABEL" in header
    assert "Accepted" in rows["card-a"]
    assert rows["card-a"].endswith("--")
    assert "Accepted" in rows["card-b"]


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
    rows = {
        line.split()[0]: line
        for line in report.splitlines()[1:]
        if line.strip() and not line.startswith(("Charger local list:", "Sync:"))
    }

    assert header.index("ENERGY") < header.index("CURRENT AUTH") < header.index("LABEL") < header.index("CACHE")
    assert "Accepted" in rows["card-a"]
    assert "Alice" in rows["card-a"]
    assert "Blocked" in rows["card-b"]
    assert "Bob" in rows["card-b"]
    assert "Invalid" not in rows["card-c"]
    assert "Accepted" in rows["card-c"]
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

    assert "CURRENT AUTH" in header
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

    assert header.split() == ["RFID", "TXNS", "ENERGY", "LAST", "SEEN", "CURRENT", "AUTH", "LABEL"]


@pytest.mark.asyncio
async def test_summary_reports_current_sync_when_file_hash_matches_known_cache(monkeypatch, tmp_path):
    (tmp_path / "rfid.csv").write_text(
        "rfid,name,enabled\ncard-a,Alice,true\ncard-b,Bob,false\n",
        encoding="utf-8",
    )
    archive = TransactionArchive(tmp_path)
    transaction_id = await archive.start("charger-a", start_payload(id_tag="card-a"))
    await archive.stop("charger-a", stop_payload(transaction_id, meter_stop=2000))

    expected_hash = rfid_module._list_hash(
        [{"rfid": "card-a", "name": "Alice", "enabled": True}]
    )
    monkeypatch.setattr(
        rfid_module,
        "resolve_rfid_cache_sync",
        lambda data_dir: cache_state(
            version=7,
            list_hash=expected_hash,
            entries=(RFIDListEntrySnapshot("card-a", "Alice", True),),
        ),
    )

    report = run_rfid(args(tmp_path, None))

    assert "Charger local list: version 7" in report
    assert "Sync:" in report and "current" in report


@pytest.mark.asyncio
async def test_summary_reports_differs_when_file_hash_differs_from_known_cache(monkeypatch, tmp_path):
    (tmp_path / "rfid.csv").write_text("card-a,Alice,true\n", encoding="utf-8")
    archive = TransactionArchive(tmp_path)
    transaction_id = await archive.start("charger-a", start_payload(id_tag="card-a"))
    await archive.stop("charger-a", stop_payload(transaction_id, meter_stop=2000))

    monkeypatch.setattr(
        rfid_module,
        "resolve_rfid_cache_sync",
        lambda data_dir: cache_state(
            version=7,
            list_hash="different",
            entries=(RFIDListEntrySnapshot("card-a", "Alice", True),),
        ),
    )

    report = run_rfid(args(tmp_path, None))

    assert "Charger local list: version 7" in report
    assert "Sync:" in report and "differs" in report


@pytest.mark.asyncio
async def test_summary_reports_unknown_sync_for_unrecognized_live_cache(monkeypatch, tmp_path):
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

    assert "Charger local list: version 9" in report
    assert "Sync:" in report and "unknown" in report


@pytest.mark.asyncio
async def test_summary_with_cache_but_no_file_omits_sync_line(monkeypatch, tmp_path):
    archive = TransactionArchive(tmp_path)
    transaction_id = await archive.start("charger-a", start_payload(id_tag="card-a"))
    await archive.stop("charger-a", stop_payload(transaction_id, meter_stop=2000))

    monkeypatch.setattr(
        rfid_module,
        "resolve_rfid_cache_sync",
        lambda data_dir: cache_state(
            version=7,
            entries=(RFIDListEntrySnapshot("card-a", "Alice", True),),
        ),
    )

    report = run_rfid(args(tmp_path, None))

    assert "Charger local list: version 7" in report
    assert "Sync:" not in report


@pytest.mark.asyncio
async def test_summary_without_live_cache_omits_cache_summary(monkeypatch, tmp_path):
    (tmp_path / "rfid.csv").write_text("card-a,Alice,true\n", encoding="utf-8")
    archive = TransactionArchive(tmp_path)
    transaction_id = await archive.start("charger-a", start_payload(id_tag="card-a"))
    await archive.stop("charger-a", stop_payload(transaction_id, meter_stop=2000))

    monkeypatch.setattr(rfid_module, "resolve_rfid_cache_sync", lambda data_dir: None)

    report = run_rfid(args(tmp_path, None))

    assert "Charger local list:" not in report
    assert "Sync:" not in report


@pytest.mark.asyncio
async def test_summary_with_no_cache_history_omits_cache_summary(monkeypatch, tmp_path):
    (tmp_path / "rfid.csv").write_text("card-a,Alice,true\n", encoding="utf-8")
    archive = TransactionArchive(tmp_path)
    transaction_id = await archive.start("charger-a", start_payload(id_tag="card-a"))
    await archive.stop("charger-a", stop_payload(transaction_id, meter_stop=2000))

    monkeypatch.setattr(
        rfid_module,
        "resolve_rfid_cache_sync",
        lambda data_dir: cache_state(version=3, known=False, has_history=False),
    )

    report = run_rfid(args(tmp_path, None))

    assert "Charger local list:" not in report
    assert "Sync:" not in report
