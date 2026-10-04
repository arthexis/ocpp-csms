from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

from ocpp_csms.schema import (
    CURRENT_SCHEMA_VERSION,
    DATABASE_FILENAME,
    can_upgrade_schema,
    inspect_schema,
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
    return action


def connection_markers(data_dir: str | Path, expected: tuple[str, ...]) -> dict[str, int]:
    root = Path(data_dir).expanduser()
    database = root / DATABASE_FILENAME
    markers = {charger: 0 for charger in expected}
    if not expected or not database.exists():
        return markers
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
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
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Installer cutover helpers")
    subcommands = parser.add_subparsers(dest="command", required=True)

    check = subcommands.add_parser("schema-check")
    check.add_argument("--data-dir", required=True)

    upgrade = subcommands.add_parser("schema-upgrade")
    upgrade.add_argument("--data-dir", required=True)

    capture = subcommands.add_parser("capture-baseline")
    capture.add_argument("--data-dir", required=True)
    capture.add_argument("--preflight-json", required=True)
    capture.add_argument("--output", required=True)

    reconnect = subcommands.add_parser("wait-reconnect")
    reconnect.add_argument("--data-dir", required=True)
    reconnect.add_argument("--baseline", required=True)
    reconnect.add_argument("--timeout", type=float, default=30.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "schema-check":
            print(schema_action(args.data_dir))
            return 0
        if args.command == "schema-upgrade":
            print(apply_schema_action(args.data_dir))
            return 0
        if args.command == "capture-baseline":
            payload = json.loads(Path(args.preflight_json).read_text(encoding="utf-8"))
            expected = tuple(str(value) for value in payload.get("connected_chargers", ()))
            markers = connection_markers(args.data_dir, expected)
            Path(args.output).write_text(json.dumps(markers, sort_keys=True), encoding="utf-8")
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
