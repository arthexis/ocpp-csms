"""Hermetic schema-version and read-only guard tests for staged deployments."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / "tests/deployment/schema_database_guard.py"
STAGED = ROOT / "tests/deployment/staged_dependency_isolation.sh"


def make_db(home: Path, directory_version: int, sqlite_version: int) -> Path:
    db = home / "ocpp-csms-data" / "schemas" / str(directory_version) / "ocpp-csms.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as conn:
        conn.execute(f"PRAGMA user_version = {sqlite_version}")
    return db


def execute_guard(home: Path, version: str | None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env.pop("TEST_SCHEMA", None)
    if version is not None:
        env["TEST_SCHEMA"] = version
    return subprocess.run([sys.executable, str(GUARD)], env=env, capture_output=True, text=True, check=False)


@pytest.mark.parametrize("version", [8, 9])
def test_schema_version_resolves_without_database_writes(tmp_path: Path, version: int) -> None:
    home = tmp_path / "home # with spaces"
    db = make_db(home, version, version)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    result = execute_guard(home, str(version))
    assert (result.returncode, result.stdout.strip(), result.stderr) == (0, str(db), "")
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert {p.name for p in db.parent.iterdir()} == {db.name}


@pytest.mark.parametrize("raw", [None, "", "0", "-1", "+9", "08", "1.0", "9/../8", "9\n", " 9", "999999999999999999999999", "１２"])
def test_invalid_schema_fails_without_database_access(tmp_path: Path, raw: str | None) -> None:
    home = tmp_path / "home"
    db = make_db(home, 8, 8)
    before = db.read_bytes()
    result = execute_guard(home, raw)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "TEST_SCHEMA" in result.stderr
    assert db.read_bytes() == before


@pytest.mark.parametrize("requested,stored", [(8, 9), (9, 8)])
def test_rejects_incompatible_sqlite_user_version(tmp_path: Path, requested: int, stored: int) -> None:
    db = make_db(tmp_path, requested, stored)
    before = db.read_bytes()
    result = execute_guard(tmp_path, str(requested))
    assert result.returncode == 2 and "user_version mismatch" in result.stderr
    assert result.stdout == ""
    assert db.read_bytes() == before


def test_missing_version_does_not_fall_back_to_previous_schema(tmp_path: Path) -> None:
    db = make_db(tmp_path, 8, 8)
    before = db.read_bytes()
    result = execute_guard(tmp_path, "9")
    assert result.returncode == 2 and "missing" in result.stderr
    assert result.stdout == ""
    assert db.read_bytes() == before
    assert not (tmp_path / "ocpp-csms-data/schemas/9").exists()


def test_invalid_sqlite_file_fails_closed(tmp_path: Path) -> None:
    db = tmp_path / "ocpp-csms-data" / "schemas" / "8" / "ocpp-csms.sqlite3"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"not a SQLite file")
    result = execute_guard(tmp_path, "8")
    assert result.returncode == 2 and result.stdout == ""
    assert db.read_bytes() == b"not a SQLite file"


@pytest.mark.parametrize("scenario", ["unset", "invalid", "missing", "mismatch"])
def test_fixture_rejects_before_git_or_host_commands(tmp_path: Path, scenario: str) -> None:
    home = tmp_path / "home"
    make_db(home, 8, 9 if scenario == "mismatch" else 8)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "host-command-called"
    for command in ("git", "sudo", "systemctl", "stat", "readlink"):
        script = bin_dir / command
        script.write_text('#!/bin/sh\nprintf "invoked\\n" >> "$GUARD_CALL_LOG"\nexit 99\n')
        script.chmod(0o755)
    env = os.environ.copy()
    env.update({"HOME": str(home), "GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted", "GUARD_CALL_LOG": str(log), "PATH": str(bin_dir) + os.pathsep + env["PATH"]})
    env.pop("TEST_SCHEMA", None)
    if scenario == "invalid":
        env["TEST_SCHEMA"] = "8/../9"
    elif scenario == "missing":
        env["TEST_SCHEMA"] = "9"
    elif scenario == "mismatch":
        env["TEST_SCHEMA"] = "8"
    result = subprocess.run(["bash", str(STAGED)], env=env, cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 2, result.stderr
    assert not log.exists(), "host command was invoked before schema validation"


@pytest.mark.parametrize("version", [8, 9])
def test_active_wal_header_is_read_without_checkpoint_or_database_write(tmp_path: Path, version: int) -> None:
    home = tmp_path / "active-wal-home"
    db = home / "ocpp-csms-data" / "schemas" / str(version) / "ocpp-csms.sqlite3"
    db.parent.mkdir(parents=True)
    # The initial schema and user_version are still in WAL while the writer is connected.
    with sqlite3.connect(db) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        writer.execute("CREATE TABLE tx (id INTEGER PRIMARY KEY)")
        writer.execute(f"PRAGMA user_version={version}")
        writer.execute("INSERT INTO tx VALUES (1)")
        writer.commit()
        assert (db.parent / (db.name + "-wal")).exists()
        before_db = db.read_bytes()
        before_wal = (db.parent / (db.name + "-wal")).read_bytes()
        result = execute_guard(home, str(version))
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == str(db)
        assert db.read_bytes() == before_db
        assert (db.parent / (db.name + "-wal")).read_bytes() == before_wal
