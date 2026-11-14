"""Fail-closed, read-only contract between Discover and the running CSMS writer.

The active service process, not an old schema directory or a release candidate,
selects the database. This check performs no SQLite schema initialization.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3
import subprocess

from ocpp_csms.schema import DATABASE_FILENAME

_REQUIRED_TABLES = frozenset({"events", "discovery_sessions", "discovery_event_sessions"})


def validate_storage(data_dir: str | Path, pid: int, *, proc_root: Path = Path("/proc")) -> Path:
    """Verify the live CSMS writer uses a readable evidence database at data_dir."""
    if pid <= 0:
        raise RuntimeError("csms_storage_contract: CSMS has no active MainPID")
    try:
        argv = (proc_root / str(pid) / "cmdline").read_bytes().split(b"\0")
        argv = [part.decode("utf-8") for part in argv if part]
    except (OSError, UnicodeError) as exc:
        raise RuntimeError("csms_storage_contract: cannot inspect CSMS MainPID") from exc
    values = [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == "--data-dir"]
    if len(values) != 1 or "serve" not in argv:
        raise RuntimeError("csms_storage_contract: running CSMS data directory is ambiguous")
    expected = Path(data_dir).expanduser().resolve(strict=True)
    actual = Path(values[0]).expanduser().resolve(strict=True)
    if actual != expected:
        raise RuntimeError(
            f"csms_storage_contract: Discover {expected} differs from running CSMS {actual}"
        )
    database = expected / DATABASE_FILENAME
    if not database.is_file():
        raise RuntimeError(f"csms_storage_contract: active SQLite database missing: {database}")
    try:
        with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )}
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    except sqlite3.Error as exc:
        raise RuntimeError("csms_storage_contract: active SQLite evidence unreadable") from exc
    if version < 1 or not _REQUIRED_TABLES.issubset(tables):
        raise RuntimeError("csms_storage_contract: active SQLite evidence tables/schema missing")
    return expected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Discover against the live CSMS storage.")
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args(argv)
    try:
        result = subprocess.run(
            ["systemctl", "show", "ocpp-csms.service", "-p", "MainPID", "--value"],
            capture_output=True, text=True, check=True,
        )
        validate_storage(args.data_dir, int(result.stdout.strip()))
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"{exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
