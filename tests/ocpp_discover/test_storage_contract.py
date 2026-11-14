"""Hermetic active-schema contract tests: no systemctl, services, or firewall."""
from pathlib import Path
import sqlite3

import pytest

from ocpp_discover.storage_contract import validate_storage

def _fixture(tmp_path: Path, version: int = 8):
    root = tmp_path / "ocpp-csms-data"
    active = root / "schemas" / str(version)
    active.mkdir(parents=True)
    db = active / "ocpp-csms.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute(f"PRAGMA user_version={version}")
        for table in ("events", "discovery_sessions", "discovery_event_sessions"):
            conn.execute(f"CREATE TABLE {table} (id INTEGER)")
    proc = tmp_path / "proc" / "123"
    proc.mkdir(parents=True)
    proc.joinpath("cmdline").write_bytes(
        b"/opt/current/venv/bin/ocpp-csms\0--data-dir\0"
        + str(active).encode() + b"\0serve\0"
    )
    return root, active, db, tmp_path / "proc", proc

@pytest.mark.parametrize("version", [8, 9, 12])
def test_schema_selected_by_running_service_not_hardcoded(tmp_path, version):
    root, active, db, proc, _ = _fixture(tmp_path, version)
    assert validate_storage(active, 123, proc_root=proc) == active
    with pytest.raises(RuntimeError, match="differs"):
        validate_storage(root, 123, proc_root=proc)
    assert not (root / "ocpp-csms.sqlite3").exists()

def test_missing_database_does_not_create_fallback(tmp_path):
    root, active, db, proc, _ = _fixture(tmp_path)
    db.unlink()
    with pytest.raises(RuntimeError, match="missing"):
        validate_storage(active, 123, proc_root=proc)
    assert not db.exists()

def test_missing_evidence_tables_fails_closed(tmp_path):
    root, active, db, proc, _ = _fixture(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE discovery_event_sessions")
    with pytest.raises(RuntimeError, match="tables/schema"):
        validate_storage(active, 123, proc_root=proc)

def test_unavailable_or_ambiguous_writer_fails_closed(tmp_path):
    root, active, db, proc, process = _fixture(tmp_path)
    with pytest.raises(RuntimeError, match="MainPID"):
        validate_storage(active, 0, proc_root=proc)
    process.joinpath("cmdline").write_bytes(b"python\0--data-dir\0" + str(active).encode() + b"\0")
    with pytest.raises(RuntimeError, match="ambiguous"):
        validate_storage(active, 123, proc_root=proc)

def test_live_schema_switch_and_reversal_follow_writer(tmp_path):
    root, active, db, proc, process = _fixture(tmp_path, 8)
    new = root / "schemas" / "9"
    new.mkdir()
    with sqlite3.connect(new / "ocpp-csms.sqlite3") as conn:
        conn.execute("PRAGMA user_version=9")
        for table in ("events", "discovery_sessions", "discovery_event_sessions"):
            conn.execute(f"CREATE TABLE {table} (id INTEGER)")
    process.joinpath("cmdline").write_bytes(
        b"ocpp-csms\0--data-dir\0" + str(new).encode() + b"\0serve\0"
    )
    assert validate_storage(new, 123, proc_root=proc) == new
    with pytest.raises(RuntimeError, match="differs"):
        validate_storage(active, 123, proc_root=proc)
    process.joinpath("cmdline").write_bytes(
        b"ocpp-csms\0--data-dir\0" + str(active).encode() + b"\0serve\0"
    )
    assert validate_storage(active, 123, proc_root=proc) == active

def test_role_guards_before_persistent_network_mutation():
    repo = Path(__file__).resolve().parents[2]
    defaults = (repo / "ansible/roles/ocpp_discover/defaults/main.yml").read_text()
    tasks = (repo / "ansible/roles/ocpp_discover/tasks/main.yml").read_text()
    unit = (repo / "ansible/roles/ocpp_discover/templates/ocpp-discover.service.j2").read_text()
    assert "ocpp_csms_service_data_dir | mandatory" in defaults
    assert tasks.index("Verify running CSMS storage") < tasks.index("Install stable OCPP Discover command")
    assert tasks.index("Verify running CSMS storage") < tasks.index("Prepare OCPP Discover persistent integration")
    assert "ExecStartPre=" in unit and "ocpp_discover.storage_contract" in unit
    assert "OCPP_DISCOVER_ALLOW_ROUTED_HTTP_80" not in unit
