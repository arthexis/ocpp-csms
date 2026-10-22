"""Previously received diagnostic uploads remain discoverable without automatic deletion."""
import json
from datetime import datetime, timedelta, timezone

from ocpp_csms import diagnostic_upload


def test_received_archive_and_request_metadata_are_preserved(monkeypatch, tmp_path):
    root = tmp_path / "uploads"
    token = "a" * 40
    folder = root / token
    folder.mkdir(parents=True)
    (folder / "upload.bin").write_bytes(b"archive")
    records = tmp_path / "data" / "diagnostics-requests"
    records.mkdir(parents=True)
    created = datetime(2026, 10, 1, tzinfo=timezone.utc)
    record = records / f"{token}.json"
    record.write_text(json.dumps({
        "id": token, "charger": "CP1", "created_at": created.isoformat(),
        "expires_at": (created + timedelta(hours=2)).isoformat(),
        "result": "requested",
    }))
    monkeypatch.setattr(diagnostic_upload, "ROOT", root)
    monkeypatch.setattr(diagnostic_upload, "RETAINED", root / "retained")
    monkeypatch.setattr(diagnostic_upload, "_now", lambda: created + timedelta(days=45))
    monkeypatch.setattr(diagnostic_upload, "_notifications", lambda *a: [])
    assert diagnostic_upload.list_requests(tmp_path / "data")[0]["result"] == "received"
    assert (folder / "upload.bin").read_bytes() == b"archive"
    assert record.exists()
