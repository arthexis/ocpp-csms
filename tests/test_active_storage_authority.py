"""Read-only, hermetic active storage authority against fake /proc and systemd."""
import json
from pathlib import Path
from subprocess import CompletedProcess, CalledProcessError
from unittest.mock import patch

import pytest

from ocpp_csms import active_storage as authority


def fixture(tmp_path, *, running="8", candidate="9"):
    root = tmp_path / "ocpp-csms-data"
    for schema in (running, candidate):
        p = root / "schemas" / schema
        p.mkdir(parents=True)
        (p / "ocpp-csms.sqlite3").touch()
    proc = tmp_path / "proc"
    (proc / "321").mkdir(parents=True)
    actual = root / "schemas" / running
    (proc / "321" / "cmdline").write_bytes(
        b"/usr/bin/python3\0-m\0ocpp_csms\0--data-dir\0" +
        str(actual).encode() + b"\0serve\0"
    )
    return root, proc, actual


def mock_systemctl(path, *, load="loaded", state="active", pid="321", unit_path=None):
    entries = {
        "LoadState": load, "ActiveState": state, "MainPID": pid,
        "ExecStart": (
            "{ path=/usr/local/bin/ocpp-csms ; argv[]=/usr/local/bin/ocpp-csms --data-dir "
            + str(unit_path or path) + " serve ; }"
        ),
    }
    def execute(argv, **kwargs):
        assert argv[:3] == ["systemctl", "show", "ocpp-csms.service"]
        return CompletedProcess(argv, 0, entries[argv[4]] + "\n")
    return execute


def test_prepared_schema_is_not_active_when_writer_is_previous_schema(tmp_path):
    root, proc, live = fixture(tmp_path)
    with patch.object(authority.subprocess, "run", side_effect=mock_systemctl(live)):
        assert authority.active_storage(storage_root=root, proc_root=proc) == live
    assert (root / "schemas" / "9" / "ocpp-csms.sqlite3").exists()


def test_same_schema_writer_is_reused(tmp_path):
    root, proc, active = fixture(tmp_path, running="9", candidate="8")
    with patch.object(authority.subprocess, "run", side_effect=mock_systemctl(active)):
        assert authority.active_storage(storage_root=root, proc_root=proc) == active


def test_absent_unit_is_first_install_only(tmp_path):
    root, proc, active = fixture(tmp_path)
    with patch.object(authority.subprocess, "run",
                      side_effect=mock_systemctl(active, load="not-found")):
        assert authority.active_storage(storage_root=root, proc_root=proc) is None


@pytest.mark.parametrize("condition", [
    {"state": "inactive"}, {"pid": "0"}, {"pid": "bad"}, {"load": "masked"},
    {"unit_path": "wrong"},
])
def test_fail_closed_when_writer_or_unit_ambiguous(tmp_path, condition):
    root, proc, active = fixture(tmp_path)
    with patch.object(authority.subprocess, "run",
                      side_effect=mock_systemctl(active, **condition)):
        with pytest.raises((RuntimeError, FileNotFoundError)):
            authority.active_storage(storage_root=root, proc_root=proc)


def test_fail_closed_when_proc_is_missing(tmp_path):
    root, proc, active = fixture(tmp_path)
    (proc / "321" / "cmdline").unlink()
    with patch.object(authority.subprocess, "run", side_effect=mock_systemctl(active)):
        with pytest.raises(RuntimeError, match="cannot inspect CSMS MainPID"):
            authority.active_storage(storage_root=root, proc_root=proc)


def test_cli_emits_path_and_never_mutates_candidate(tmp_path, capsys):
    root, proc, active = fixture(tmp_path)
    with patch.object(authority, "active_storage", return_value=active) as check:
        assert authority.main(["--storage-root", str(root)]) == 0
    assert json.loads(capsys.readouterr().out) == {"active": str(active)}
    check.assert_called_once()
