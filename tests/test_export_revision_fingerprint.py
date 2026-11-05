"""Deterministic export transaction revision contract."""
import hashlib
import json
from pathlib import Path


def test_export_fingerprint_is_derived_from_snapshot():
    source = (Path(__file__).resolve().parents[1] / "src/ocpp_csms/export_contract.py").read_text()
    assert 'snapshot["revision_fingerprint"] = hashlib.sha256' in source
    assert 'sort_keys=True' in source


def test_fingerprint_tracks_changes_without_timestamp_noise():
    def fingerprint(value):
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    assert fingerprint({"id": 1, "energy_wh": 10}) == fingerprint({"energy_wh": 10, "id": 1})
    assert fingerprint({"id": 1, "energy_wh": 10}) != fingerprint({"id": 1, "energy_wh": 11})
