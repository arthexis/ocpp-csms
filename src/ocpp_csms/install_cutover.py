from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from ocpp_csms.cutover_evidence import has_committed_ocpp_evidence
from ocpp_csms.schema_storage import discover_active_storage, plan_storage, prepare_storage, activate_schema
from ocpp_csms.schema import (
    CURRENT_SCHEMA_VERSION,
    can_upgrade_schema,
    inspect_schema,
    ensure_wal,
    upgrade_schema,
)


def schema_action(data_dir: str | Path) -> str:
    """Return the safe cutover action for the existing database."""
    info = inspect_schema(data_dir)
    if not info.exists:
        return "create"
    if info.version == CURRENT_SCHEMA_VERSION:
        return "current"
    if info.version == 0:
        raise RuntimeError("events database is unversioned")
    if info.version is not None and info.version > CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"events database schema {info.version} is newer than supported {CURRENT_SCHEMA_VERSION}"
        )
    if can_upgrade_schema(info):
        return "upgrade"
    raise RuntimeError(
        f"no supported schema upgrade from {info.version} to {CURRENT_SCHEMA_VERSION}"
    )


def apply_schema_action(data_dir: str | Path) -> str:
    action = schema_action(data_dir)
    if action == "upgrade":
        upgrade_schema(data_dir)
    if action != "create":
        ensure_wal(data_dir)
    return action


def verify_schema_integrity(data_dir: str | Path) -> None:
    """Refuse activation if the database is not current or is damaged."""
    info = inspect_schema(data_dir)
    if info.version != CURRENT_SCHEMA_VERSION:
        raise RuntimeError(f"schema verification failed: expected {CURRENT_SCHEMA_VERSION}, got {info.version}")
    with closing(sqlite3.connect(f"file:{info.path}?mode=ro", uri=True)) as connection:
        mode = connection.execute("PRAGMA journal_mode").fetchone()
        if mode is None or str(mode[0]).lower() != "wal":
            raise RuntimeError(f"database WAL verification failed: {mode}")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise RuntimeError(f"database integrity check failed: {integrity}")


def checkpoint_stopped_storage(data_dir: str | Path) -> None:
    """Checkpoint a stopped legacy database before moving its directory.

    Caller must have stopped the service; a busy checkpoint is a hard failure.
    No schema upgrade or migration is performed here.
    """
    info = inspect_schema(data_dir)
    if not info.exists:
        raise RuntimeError(f"database missing for checkpoint: {info.path}")
    with closing(sqlite3.connect(f"{info.path.resolve().as_uri()}?mode=rw", uri=True, timeout=0.2)) as db:
        result = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if result is None or result[0] != 0:
            raise RuntimeError(f"database WAL checkpoint incomplete: {result}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Installer cutover helpers")
    subcommands = parser.add_subparsers(dest="command", required=True)

    check = subcommands.add_parser("schema-check")
    check.add_argument("--data-dir", required=True)

    upgrade = subcommands.add_parser("schema-upgrade")
    upgrade.add_argument("--data-dir", required=True)

    plan = subcommands.add_parser("storage-plan")
    plan.add_argument("--storage-root", required=True)
    plan.add_argument("--active-data-dir")

    prepare = subcommands.add_parser("storage-prepare")
    prepare.add_argument("--storage-root", required=True)
    prepare.add_argument("--active-data-dir")

    activate = subcommands.add_parser("storage-activate")
    activate.add_argument("--storage-root", required=True)
    activate.add_argument("--target", required=True)

    checkpoint = subcommands.add_parser("storage-checkpoint")
    checkpoint.add_argument("--data-dir", required=True)

    evidence = subcommands.add_parser("storage-evidence")
    evidence.add_argument("--data-dir", required=True)

    verify = subcommands.add_parser("schema-verify")
    verify.add_argument("--data-dir", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command in ("storage-plan", "storage-prepare"):
            active = (args.active_data_dir if args.active_data_dir is not None
                      else discover_active_storage(args.storage_root))
            plan = plan_storage(args.storage_root, active)
            if args.command == "storage-prepare":
                prepare_storage(plan)
            print(json.dumps({"action": plan.action, "target": str(plan.target),
                              "active_schema": plan.active_schema,
                              "active": str(plan.active) if plan.active else None}, sort_keys=True))
            return 0
        if args.command == "storage-activate":
            print(str(activate_schema(args.storage_root, args.target)))
            return 0
        if args.command == "storage-checkpoint":
            checkpoint_stopped_storage(args.data_dir)
            print("ok")
            return 0
        if args.command == "storage-evidence":
            committed = has_committed_ocpp_evidence(args.data_dir)
            print(json.dumps({"committed_ocpp_evidence": committed}))
            return 0
        if args.command == "schema-check":
            print(schema_action(args.data_dir))
            return 0
        if args.command == "schema-upgrade":
            print(apply_schema_action(args.data_dir))
            return 0
        if args.command == "schema-verify":
            verify_schema_integrity(args.data_dir)
            print("ok")
            return 0
        raise RuntimeError(f"unsupported installer command: {args.command}")
    except (RuntimeError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
