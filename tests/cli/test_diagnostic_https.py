"""HTTPS diagnostic upload request lifecycle and CLI parser regression."""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

from ocpp_csms.cli import build_parser
from ocpp_csms import diagnostic_upload


def test_diagnose_subcommands_parse_without_legacy_upload_url():
    parser, _ = build_parser()
    args = parser.parse_args(["diagnose", "request", "--cp", "ABB001"])
    assert args.command == "diagnose"
    assert args.diagnostic_operation == "request"
    assert args.charger == "ABB001"
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
    record, location = diagnostic_upload.prepare(tmp_path, "ABB001")
    assert location.startswith("https://charger.example.com/ocpp-diagnostics/")
    assert location.endswith("/upload.bin")
    assert record["charger"] == "ABB001"
    diagnostic_upload.record_response(tmp_path, record["id"], {"file_name": "diag.zip"})
    target = root / record["id"] / "upload.bin"
    target.write_bytes(b"diagnostics")
    found = diagnostic_upload.list_requests(tmp_path, charger="ABB001")
    assert len(found) == 1
    assert found[0]["result"] == "received"
    assert found[0]["suggested_file"] == "diag.zip"
    assert found[0]["files"] == [{"name": "upload.bin", "bytes": 11}]
    assert diagnostic_upload.list_requests(tmp_path, charger="different") == []


def test_expired_request_is_reported(tmp_path, monkeypatch):
    root = tmp_path / "incoming"
    root.mkdir()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"hostname": "charger.example.com"}))
    monkeypatch.setattr(diagnostic_upload, "ROOT", root)
    monkeypatch.setattr(diagnostic_upload, "CONFIG", config)
    record, _ = diagnostic_upload.prepare(tmp_path, None)
    file = tmp_path / "diagnostics-requests" / (record["id"] + ".json")
    saved = json.loads(file.read_text())
    saved["expires_at"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    file.write_text(json.dumps(saved))
    assert diagnostic_upload.list_requests(tmp_path)[0]["result"] == "expired"


def test_safe_archive_inspection_reports_fault_without_extracting(tmp_path):
    import zipfile
    archive = tmp_path / "diag.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("../outside.log", "INFO starting\\nERROR ABB converter alarm\\n")
        z.writestr("binary.bin", b"\\x00\\x01\\x02")
    lines = diagnostic_upload.inspect_file(archive)
    assert any("ABB converter alarm" in line for line in lines)
    assert not (tmp_path.parent / "outside.log").exists()
