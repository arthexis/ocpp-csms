"""Safe opt-in archival of isolated CSMS test data (never removes configuration)."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
import shutil
import sqlite3
from contextlib import closing
import uuid
from pathlib import Path
import subprocess

from ocpp_csms.runtime import process_is_running
from ocpp_csms.cli.appliance import initialize_storage
from ocpp_csms.backup_archive import archive_before_reset


def add_data_command(subcommands: argparse._SubParsersAction) -> argparse.ArgumentParser:
    data = subcommands.add_parser("data", help="Inspect or archive appliance test data")
    operations = data.add_subparsers(dest="data_command", required=True)
    reset = operations.add_parser("reset", help="Archive all test data and initialize clean storage")
    target = reset.add_mutually_exclusive_group(required=True)
    target.add_argument("--all", action="store_true", help="Archive and reset the entire application data directory")
    target.add_argument("--cp", metavar="NAME", help="Delete records for only this charge-point identity")
    reset.add_argument("--dry-run", action="store_true", help="Check safety without modifying files")
    return data




def _remove_cp_from_copy(root: Path, cp: str) -> None:
    """Change only staged evidence, preserving transaction IDs of other chargers."""
    from ocpp_csms.schema import inspect_schema, require_supported_schema

    schema = inspect_schema(root)
    require_supported_schema(schema)
    # Select by recorded identity, never filename prefix (which can collide).
    for folder in ("transactions", "transactions-unresolved"):
        directory = root / folder
        if not directory.exists():
            continue
        for path in directory.rglob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ValueError("unreadable transaction evidence: reset refused") from exc
            if not isinstance(record, dict) or not isinstance(record.get("charge_point_id"), str):
                raise ValueError("ambiguous transaction evidence: reset refused")
            if record["charge_point_id"] == cp:
                path.unlink()

    with closing(sqlite3.connect(schema.path)) as db, db:
        db.execute("PRAGMA foreign_keys=ON")
        ids = [row[0] for row in db.execute(
            "SELECT transaction_id FROM transactions WHERE charger_id=?", (cp,)
        )]
        # Clear references by transaction identity; do not reuse IDs.
        if ids:
            placeholders = ",".join("?" for _ in ids)
            db.execute(f"DELETE FROM start_identities WHERE transaction_id IN ({placeholders})", ids)
        db.execute("DELETE FROM rfid_list_entries WHERE list_id IN "
                   "(SELECT id FROM rfid_lists WHERE charger_id=?)", (cp,))
        for table in ("events", "runtime_events", "connector_status",
                      "rfid_lists", "transactions"):
            db.execute(f"DELETE FROM {table} WHERE charger_id=?", (cp,))
        remaining = db.execute("PRAGMA foreign_key_check").fetchall()
        if remaining:
            raise ValueError("remaining database references: reset refused")
    # Keep unrelated data and stable source identity intact.

def reset_live_cp(data_dir: str | Path, cp: str) -> dict:
    """Idempotently delete only the selected disconnected charge point's evidence.

    The server gates new connections for this identity while this runs.
    Retrying after interruption completes remaining deletes.
    """
    from ocpp_csms.schema import inspect_schema, require_supported_schema
    root = Path(data_dir).expanduser().absolute()
    if not cp or cp.strip() != cp or "/" in cp or "\\x00" in cp:
        raise ValueError("invalid charge-point identity")
    if root.is_symlink() or not root.is_dir():
        raise ValueError("unsafe data directory")
    info = inspect_schema(root)
    require_supported_schema(info)
    selected = []
    for folder in ("transactions", "transactions-unresolved"):
        parent = root / folder
        if not parent.exists():
            continue
        for path in parent.rglob("*"):
            if path.is_symlink():
                raise ValueError("symlinked evidence: reset refused")
            if path.suffix != ".json" or not path.is_file():
                continue
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ValueError("unreadable transaction evidence: reset refused") from exc
            if not isinstance(record, dict) or not isinstance(record.get("charge_point_id"), str):
                raise ValueError("ambiguous transaction evidence: reset refused")
            if record["charge_point_id"] == cp:
                selected.append(path)
    # Refuse unsafe requests before snapshotting.
    with closing(sqlite3.connect(info.path, timeout=5)) as preflight:
        if preflight.execute("SELECT 1 FROM transactions WHERE charger_id=? AND state NOT IN ('stopped', 'complete', 'completed') LIMIT 1", (cp,)).fetchone():
            raise ValueError("charge point has an open transaction; reset refused")
    archive_path = archive_before_reset(root, scope="cp", cp=cp)
    with closing(sqlite3.connect(info.path, timeout=5)) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        try:
            if db.execute("SELECT 1 FROM transactions WHERE charger_id=? "
                          "AND state NOT IN ('stopped', 'complete', 'completed') LIMIT 1", (cp,)).fetchone():
                raise ValueError("charge point has an open transaction; reset refused")
            ids = [row[0] for row in db.execute(
                "SELECT transaction_id FROM transactions WHERE charger_id=?", (cp,))]
            if ids:
                marks = ",".join("?" for _ in ids)

                db.execute(f"DELETE FROM start_identities WHERE transaction_id IN ({marks})", ids)
            db.execute("DELETE FROM rfid_list_entries WHERE list_id IN "
                       "(SELECT id FROM rfid_lists WHERE charger_id=?)", (cp,))
            for table in ("events", "runtime_events", "connector_status", "rfid_lists", "transactions"):
                db.execute(f"DELETE FROM {table} WHERE charger_id=?", (cp,))
            if db.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("foreign key violation: reset refused")
            db.commit()
        except Exception:
            db.rollback()
            raise
    # A crash here leaves some JSON behind; the next invocation removes it.
    for path in selected:
        path.unlink(missing_ok=True)
    return {"decision": "reset", "scope": "cp", "cp": cp,
            "deleted_json": len(selected), "data_dir": str(root), "archive": str(archive_path),
            "preserve": "other charge points, allocator and authentication"}

def reset_test_data(data_dir: str | Path, *, all_data: bool = False, cp: str | None = None, dry_run: bool = False) -> dict:
    if all_data == (cp is not None):
        raise ValueError("specify exactly one of --all or --cp NAME")
    if cp is not None and (not cp.strip() or cp != cp.strip() or "/" in cp or "\\x00" in cp):
        raise ValueError("invalid charge-point identity")
    root = Path(data_dir).expanduser().absolute()
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise ValueError("refusing unsafe or symlinked data directory")
    if not root.exists():
        raise ValueError("data directory does not exist; use ocpp-csms init instead")
    # Use the on-disk PID as well as systemd state. Neither a live listener nor
    # a running unit is safe to reset even when there are no active transactions.
    if process_is_running(root):
        raise ValueError("CSMS process is running; stop the service before reset")
    try:
        unit = subprocess.run(["systemctl", "is-active", "--quiet", "ocpp-csms.service"],
                              check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        unit = None
    if unit is not None and unit.returncode == 0:
        raise ValueError("ocpp-csms.service is active; stop it before reset")
    from ocpp_csms.schema import inspect_schema, require_supported_schema
    schema = inspect_schema(root)
    if schema.exists:
        require_supported_schema(schema)
    if root.resolve() in (Path.home().resolve(), Path("/")):
        raise ValueError("refusing reset of home or filesystem root")
    backup_root = root.parent / (root.name + "-test-backups")
    if backup_root.is_symlink():
        raise ValueError("refusing symlinked backup directory")
    if dry_run:
        return {"decision": "dry-run", "scope": "all" if all_data else "cp",
                "cp": cp, "data_dir": str(root), "backup_parent": str(backup_root),
                "preserve": "deployment profile, nginx/TLS and Basic authentication configuration"}
    archive_path = archive_before_reset(root, scope="all" if all_data else "cp", cp=cp)
    backup_root.mkdir(mode=0o700, exist_ok=True)
    os.chmod(backup_root, 0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = backup_root / stamp
    if backup.exists():
        raise ValueError("backup destination already exists")
    if cp is not None:
        staging = root.parent / (root.name + "-reset-stage-" + uuid.uuid4().hex)
        try:
            # Refuse to follow symlinks in a source directory containing evidence.
            if any(path.is_symlink() for path in root.rglob("*")):
                raise ValueError("refusing symlinked evidence paths")
            # Copy non-SQLite evidence normally, but snapshot the database through
            # SQLite so committed WAL frames cannot be lost or copied inconsistently.
            from ocpp_csms.schema import database_path, inspect_schema, require_supported_schema
            info = inspect_schema(root)
            require_supported_schema(info)
            db_name = info.path.name
            shutil.copytree(
                root, staging,
                ignore=lambda directory, names: [
                    name for name in names
                    if Path(directory) == root and name in
                    (db_name, db_name + "-wal", db_name + "-shm")
                ],
            )
            with closing(sqlite3.connect(f"file:{info.path}?mode=ro", uri=True)) as source:
                with closing(sqlite3.connect(staging / db_name)) as destination:
                    source.backup(destination)
            _remove_cp_from_copy(staging, cp)
            with closing(sqlite3.connect(staging / db_name)) as check:
                if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("staged database integrity check failed")
            root.rename(backup)
            try:
                staging.rename(root)
            except Exception:
                backup.rename(root)
                raise
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return {"decision": "reset", "scope": "cp", "cp": cp,
                "backup": str(backup), "archive": str(archive_path), "data_dir": str(root),
                "preserve": "other charge points, deployment and authentication configuration"}
    root.rename(backup)  # same parent filesystem; no destructive deletion
    try:
        initialize_storage(str(root))
    except Exception:
        # Never destroy archived evidence even if initialization fails.
        if root.exists() and any(root.iterdir()):
            raise RuntimeError("reinitialization failed; backup preserved; inspect partial new data before restoring")
        if root.exists():
            root.rmdir()
        backup.rename(root)
        raise
    return {"decision": "reset", "scope": "all", "backup": str(backup), "archive": str(archive_path), "data_dir": str(root),
            "preserve": "deployment profile, nginx/TLS and Basic authentication configuration"}


def run_data(args: argparse.Namespace) -> int:
    if args.data_command != "reset":
        raise ValueError("unknown data operation")
    if args.cp is not None and args.dry_run:
        print(json.dumps({"decision": "dry-run", "scope": "cp", "cp": args.cp,
                          "data_dir": str(args.data_dir)}, indent=2, sort_keys=True))
        return 0
    if args.cp is not None and not args.dry_run:
        from ocpp_csms.control import send_control
        response = asyncio.run(send_control(args.data_dir, {"command": "data_reset_cp", "charger": args.cp}))
        if not response.get("ok"):
            raise ValueError(f"live reset refused: {response.get('error')}: {response.get('detail', '')}")
        print(json.dumps(response["response"], indent=2, sort_keys=True))
        return 0
    print(json.dumps(reset_test_data(args.data_dir, all_data=args.all, cp=args.cp, dry_run=args.dry_run),
                     indent=2, sort_keys=True))
    return 0
