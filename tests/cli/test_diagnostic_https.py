"""HTTPS diagnostic upload request lifecycle and CLI parser regression."""
from __future__ import annotations

import json

from ocpp_csms.cli import build_parser
from ocpp_csms import diagnostic_upload


def test_diagnose_subcommands_parse_without_legacy_upload_url():
    parser, _ = build_parser()
    args = parser.parse_args(["diagnose", "request", "--cp", "CP001"])
    assert args.command == "diagnose"
    assert args.diagnostic_operation == "request"
    assert args.charger == "CP001"
    assert parser.parse_args(["diagnose", "status"]).diagnostic_operation == "status"
    assert parser.parse_args(["diagnose", "list"]).diagnostic_operation == "list"
    assert parser.parse_args(["diagnose", "show"]).diagnostic_operation == "show"


def test_request_prepares_destination_and_indexes_received_file(tmp_path, monkeypatch):
    root = tmp_path / "incoming"
    root.mkdir()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"hostname": "charger.example.com"}))
    monkeypatch.setattr(diagnostic_upload, "ROOT", root)
    monkeypatch.setattr(diagnostic_upload, "CONFIG", config)
    record, location = diagnostic_upload.prepare(tmp_path, "CP001")
    assert location.startswith("https://charger.example.com/ocpp-diagnose/")
    assert location.endswith("/upload.bin")
    assert record["charger"] == "CP001"
    diagnostic_upload.record_response(tmp_path, record["id"], {"file_name": "diag.zip"})
    target = root / record["id"] / "upload.bin"
    target.write_bytes(b"diagnostics")
    found = diagnostic_upload.list_requests(tmp_path, charger="CP001")
    assert len(found) == 1
    assert found[0]["result"] == "received"
    assert found[0]["suggested_file"] == "diag.zip"
    assert found[0]["files"] == [{"name": "upload.bin", "bytes": 11}]
    assert diagnostic_upload.list_requests(tmp_path, charger="different") == []


def test_request_remains_pending_without_archive(tmp_path, monkeypatch):
    root = tmp_path / "incoming"
    root.mkdir()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"hostname": "charger.example.com"}))
    monkeypatch.setattr(diagnostic_upload, "ROOT", root)
    monkeypatch.setattr(diagnostic_upload, "CONFIG", config)
    record, _ = diagnostic_upload.prepare(tmp_path, None)
    assert diagnostic_upload.list_requests(tmp_path)[0]["result"] == "requested"
    assert (root / record["id"]).is_dir()


def test_safe_archive_inspection_reports_fault_without_extracting(tmp_path):
    import zipfile
    archive = tmp_path / "diag.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("../outside.log", "INFO starting\\nERROR ABB converter alarm\\n")
        z.writestr("binary.bin", b"\\x00\\x01\\x02")
    lines = diagnostic_upload.inspect_file(archive)
    assert any("ABB converter alarm" in line for line in lines)
    assert not (tmp_path.parent / "outside.log").exists()
