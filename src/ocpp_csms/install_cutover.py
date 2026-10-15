from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
import time
from pathlib import Path

from ocpp_csms.cutover_evidence import has_committed_ocpp_evidence
from ocpp_csms.schema_storage import discover_active_storage, plan_storage, prepare_storage, adopt_legacy_storage, activate_schema
from ocpp_csms.status import appliance_status
from ocpp_csms.schema import (
    CURRENT_SCHEMA_VERSION,
    DATABASE_FILENAME,
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


def connection_markers(data_dir: str | Path, expected: tuple[str, ...]) -> dict[str, int]:
    root = Path(data_dir).expanduser()
    database = root / DATABASE_FILENAME
    markers = {charger: 0 for charger in expected}
    if not expected or not database.exists():
        return markers
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
        for charger in expected:
            row = connection.execute(
                """
                SELECT COALESCE(MAX(id), 0) FROM runtime_events
                WHERE charger_id = ? AND event = 'charger_connected'
                """,
                (charger,),
            ).fetchone()
            markers[charger] = int(row[0]) if row else 0
    return markers


def _freshly_connected(data_dir: Path, markers: dict[str, int]) -> set[str]:
    database = data_dir / DATABASE_FILENAME
    if not database.exists():
        return set()
    connected: set[str] = set()
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
        for charger, marker in markers.items():
            row = connection.execute(
                """
                SELECT id, event FROM runtime_events
                WHERE charger_id = ? AND event IN ('charger_connected', 'charger_disconnected')
                ORDER BY id DESC LIMIT 1
                """,
                (charger,),
            ).fetchone()
            if row and int(row[0]) > marker and row[1] == "charger_connected":
                connected.add(charger)
    return connected


def wait_for_reconnect(
    data_dir: str | Path,
    markers: dict[str, int],
    *,
    timeout: float = 30.0,
    interval: float = 0.5,
) -> tuple[str, ...]:
    """Wait for a new connected event for every charger after its baseline marker."""
    expected = set(markers)
    if not expected:
        return ()
    root = Path(data_dir).expanduser()
    deadline = time.monotonic() + timeout
    while True:
        missing = tuple(sorted(expected - _freshly_connected(root, markers)))
        if not missing:
            return ()
        if time.monotonic() >= deadline:
            return missing
        time.sleep(interval)


def wait_for_any_connection(
    data_dir: str | Path,
    *,
    timeout: float = 30.0,
    interval: float = 0.5,
) -> str | None:
    """Wait until any charger is currently connected to this CSMS."""
    if timeout < 0 or interval <= 0:
        raise ValueError("invalid_wait_interval")
    deadline = time.monotonic() + timeout
    while True:
        chargers = [
            item.charger_id
            for item in appliance_status(data_dir).get("chargers", [])
            if item.connected
        ]
        if chargers:
            return chargers[0]
        if time.monotonic() >= deadline:
            return None
        time.sleep(min(interval, max(0.0, deadline - time.monotonic())))


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

    adopt = subcommands.add_parser("storage-adopt")
    adopt.add_argument("--storage-root", required=True)

    activate = subcommands.add_parser("storage-activate")
    activate.add_argument("--storage-root", required=True)
    activate.add_argument("--target", required=True)

    checkpoint = subcommands.add_parser("storage-checkpoint")
    checkpoint.add_argument("--data-dir", required=True)

    evidence = subcommands.add_parser("storage-evidence")
    evidence.add_argument("--data-dir", required=True)

    verify = subcommands.add_parser("schema-verify")
    verify.add_argument("--data-dir", required=True)

    capture = subcommands.add_parser("capture-baseline")
    capture.add_argument("--data-dir", required=True)
    capture.add_argument("--preflight-json", required=True)
    capture.add_argument("--output", required=True)

    reconnect = subcommands.add_parser("wait-reconnect")
    reconnect.add_argument("--data-dir", required=True)
    reconnect.add_argument("--baseline", required=True)
    reconnect.add_argument("--timeout", type=float, default=30.0)

    wait_any = subcommands.add_parser("wait-any")
    wait_any.add_argument("--data-dir", required=True)
    wait_any.add_argument("--timeout", type=float, default=30.0)
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
        if args.command == "storage-adopt":
            print(str(adopt_legacy_storage(args.storage_root)))
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
        if args.command == "capture-baseline":
            payload = json.loads(Path(args.preflight_json).read_text(encoding="utf-8"))
            expected = tuple(str(value) for value in payload.get("connected_chargers", ()))
            markers = connection_markers(args.data_dir, expected)
            Path(args.output).write_text(json.dumps(markers, sort_keys=True), encoding="utf-8")
            return 0
        if args.command == "wait-any":
            charger = wait_for_any_connection(args.data_dir, timeout=args.timeout)
            if charger is None:
                print("No charger connected")
                return 1
            print(charger)
            return 0

        markers = {
            str(key): int(value)
            for key, value in json.loads(Path(args.baseline).read_text(encoding="utf-8")).items()
        }
        missing = wait_for_reconnect(args.data_dir, markers, timeout=args.timeout)
        if missing:
            print("Missing charger(s): " + ", ".join(missing))
            return 1
        return 0
    except (RuntimeError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
