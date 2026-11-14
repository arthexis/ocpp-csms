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


# Exercise the actual CLI boundary used by ExecStartPre. Never run systemctl.
from subprocess import CalledProcessError, CompletedProcess
from unittest.mock import patch
from ocpp_discover import storage_contract


def test_main_uses_systemd_mainpid(tmp_path, capsys):
    _, active, _, _, _ = _fixture(tmp_path)
    response = CompletedProcess(["systemctl"], 0, stdout="123\n")
    with patch.object(storage_contract.subprocess, "run", return_value=response) as run, \
         patch.object(storage_contract, "validate_storage", return_value=active) as validate:
        assert storage_contract.main(["--data-dir", str(active)]) == 0
    run.assert_called_once_with(
        ["systemctl", "show", "ocpp-csms.service", "-p", "MainPID", "--value"],
        capture_output=True, text=True, check=True,
    )
    validate.assert_called_once_with(str(active), 123)
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("stdout", ["0\n", "-1\n", "garbled\n", ""])
def test_main_rejects_missing_or_invalid_pid(tmp_path, capsys, stdout):
    _, active, _, _, _ = _fixture(tmp_path)
    with patch.object(storage_contract.subprocess, "run",
                      return_value=CompletedProcess(["systemctl"], 0, stdout=stdout)):
        with pytest.raises(SystemExit) as exc:
            storage_contract.main(["--data-dir", str(active)])
    assert exc.value.code == 1
    assert capsys.readouterr().err.strip()


@pytest.mark.parametrize("failure", [
    CalledProcessError(1, "systemctl"),
    FileNotFoundError("systemctl not installed"),
    PermissionError("systemctl permission denied"),
])
def test_main_rejects_systemctl_error(tmp_path, capsys, failure):
    _, active, _, _, _ = _fixture(tmp_path)
    with patch.object(storage_contract.subprocess, "run", side_effect=failure):
        with pytest.raises(SystemExit) as exc:
            storage_contract.main(["--data-dir", str(active)])
    assert exc.value.code == 1
    assert capsys.readouterr().err.strip()


def test_main_rejects_failed_validation(tmp_path, capsys):
    _, active, _, _, _ = _fixture(tmp_path)
    with patch.object(storage_contract.subprocess, "run",
                      return_value=CompletedProcess(["systemctl"], 0, stdout="123\n")), \
         patch.object(storage_contract, "validate_storage",
                      side_effect=RuntimeError("wrong active schema")):
        with pytest.raises(SystemExit) as exc:
            storage_contract.main(["--data-dir", str(active)])
    assert exc.value.code == 1
    assert "wrong active schema" in capsys.readouterr().err


def test_proc_permission_denied_preserves_sqlite(tmp_path):
    root, active, db, proc, _ = _fixture(tmp_path)
    before = db.read_bytes()
    with patch.object(Path, "read_bytes", side_effect=PermissionError("denied")):
        with pytest.raises(RuntimeError, match="cannot inspect CSMS MainPID"):
            validate_storage(active, 123, proc_root=proc)
    assert db.read_bytes() == before
    assert not (root / "ocpp-csms.sqlite3").exists()


def test_absent_selected_directory_cannot_create_fallback(tmp_path):
    root, active, db, proc, _ = _fixture(tmp_path)
    db.unlink()
    active.rmdir()
    with pytest.raises((OSError, RuntimeError)):
        validate_storage(active, 123, proc_root=proc)
    assert not (root / "ocpp-csms.sqlite3").exists()


def test_ro_query_does_not_change_fixture_bytes_or_files(tmp_path):
    root, active, db, proc, _ = _fixture(tmp_path)
    before = (db.read_bytes(), db.stat().st_ino, sorted(str(x) for x in root.rglob("*")))
    assert validate_storage(active, 123, proc_root=proc) == active
    after = (db.read_bytes(), db.stat().st_ino, sorted(str(x) for x in root.rglob("*")))
    assert after == before


def test_preflight_and_fail_forward_order_preserved():
    repo = Path(__file__).resolve().parents[2]
    tasks = (repo / "ansible/roles/ocpp_discover/tasks/main.yml").read_text()
    cutover = (repo / "ansible/roles/ocpp_csms/tasks/cutover.yml").read_text()
    install = (repo / "ansible/playbooks/install.yml").read_text()
    unit = (repo / "ansible/roles/ocpp_discover/templates/ocpp-discover.service.j2").read_text()
    assert tasks.index("Verify running CSMS storage") < tasks.index("Install stable OCPP Discover command")
    assert tasks.index("Verify running CSMS storage") < tasks.index("Prepare OCPP Discover persistent integration")
    assert tasks.index("Verify running CSMS storage") < tasks.index("Enable nftables service for future boots")
    assert install.index("name: Deploy OCPP CSMS") < install.index("name: Deploy OCPP Discover")
    assert "ocpp_csms_candidate_writer_may_have_started: true" in cutover
    assert "Refuse automatic rollback after activation or writer start" in cutover
    assert unit.index("ExecStartPre=") < unit.index("ExecStart=")
    assert "OCPP_DISCOVER_ALLOW_ROUTED_HTTP_80" not in unit
