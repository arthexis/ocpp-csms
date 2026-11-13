"""Resolve a CI fixture SQLite database without creating or modifying it."""
from __future__ import annotations

import os
from contextlib import closing
import re
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote


def main() -> int:
    raw = os.environ.get("TEST_SCHEMA")
    if raw is None or re.fullmatch(r"[1-9][0-9]{0,9}", raw) is None:
        raise ValueError("TEST_SCHEMA must be a positive decimal integer without leading zeros")
    version = int(raw)
    if version > 2_147_483_647:
        raise ValueError("TEST_SCHEMA exceeds the SQLite user_version range")
    home = os.environ.get("HOME")
    if not home or not Path(home).is_absolute():
        raise ValueError("HOME must be an absolute path")
    db = Path(home) / "ocpp-csms-data" / "schemas" / raw / "ocpp-csms.sqlite3"
    if not db.is_file():
        raise ValueError(f"Expected schema {raw} SQLite database is missing: {db}")
    # Read-only URI observes uncheckpointed WAL headers without writing to the database.
    uri = f"file:{quote(str(db), safe='/')}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        conn.execute("PRAGMA query_only=ON")
        actual = conn.execute("PRAGMA user_version").fetchone()
    if actual is None or actual[0] != version:
        got = actual[0] if actual else "missing"
        raise ValueError(f"SQLite user_version mismatch: expected {version}, found {got} in {db}")
    print(db)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, sqlite3.Error) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        sys.exit(2)
