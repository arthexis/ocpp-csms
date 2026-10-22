"""Expired diagnostic upload tokens cannot remain usable after retention rollover."""
import json
from datetime import datetime, timedelta, timezone

from ocpp_csms import diagnostic_upload


def test_received_upload_moves_out_of_capability_path_and_is_retained(monkeypatch, tmp_path):
    now = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
    root = tmp_path / "uploads"
    metadata = tmp_path / "data" / "diagnostics-requests"
    metadata.mkdir(parents=True)
    token = "a" * 40
    folder = root / token
    folder.mkdir(parents=True)
    (folder / "upload.bin").write_bytes(b"archive")
    created = now - timedelta(hours=3)
    (metadata / f"{token}.json").write_text(json.dumps({
        "id": token, "charger": "CP1", "created_at": created.isoformat(),
        "expires_at": (created + timedelta(hours=2)).isoformat(),
        "result": "requested",
    }))
    monkeypatch.setattr(diagnostic_upload, "ROOT", root)
    monkeypatch.setattr(diagnostic_upload, "RETAINED", root / "retained")
    monkeypatch.setattr(diagnostic_upload, "_now", lambda: now)
    monkeypatch.setattr(diagnostic_upload, "_notifications", lambda *a: [])
    assert diagnostic_upload.remove_expired(tmp_path / "data") == 0
    assert not folder.exists()
    assert (root / "retained" / token / "upload.bin").read_bytes() == b"archive"
    assert diagnostic_upload.list_requests(tmp_path / "data")[0]["result"] == "received"
    monkeypatch.setattr(diagnostic_upload, "_now", lambda: now + timedelta(days=31))
    assert diagnostic_upload.remove_expired(tmp_path / "data") == 1
    assert not (root / "retained" / token).exists()
    assert not (metadata / f"{token}.json").exists()
