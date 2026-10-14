from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

from ocpp_csms.runtime import process_is_running
from ocpp_csms.schema import CURRENT_SCHEMA_VERSION, inspect_schema


@dataclass(frozen=True)
class InstallPreflight:
    allowed: bool
    connected_chargers: tuple[str, ...]
    active_chargers: tuple[str, ...]
    reason: str | None = None


def _connected_chargers(data_dir: Path, schema_version: int | None) -> tuple[str, ...]:
    if not process_is_running(data_dir) or schema_version is None:
        return ()
    database = inspect_schema(data_dir).path
    if not database.exists():
        return ()
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        rows = connection.execute(
            """
            SELECT charger_id, event
            FROM runtime_events
            WHERE charger_id IS NOT NULL
              AND event IN ('charger_connected', 'charger_disconnected')
            ORDER BY id
            """
        ).fetchall()
    latest: dict[str, str] = {}
    for charger_id, event in rows:
        latest[str(charger_id)] = str(event)
    return tuple(sorted(charger for charger, event in latest.items() if event == "charger_connected"))


def _live_active_chargers(data_dir: Path, connected: tuple[str, ...]) -> tuple[str, ...]:
    """Only current-connection connector evidence can prove charging activity."""
    if not connected:
        return ()
    database = inspect_schema(data_dir).path
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        rows = connection.execute(
            """SELECT c.charger_id, c.status FROM connector_status c
               WHERE c.connector_id > 0 AND c.received_at >= (
                   SELECT MAX(r.occurred_at) FROM runtime_events r
                   WHERE r.charger_id = c.charger_id AND r.event = 'charger_connected'
               )"""
        ).fetchall()
    return tuple(sorted({str(charger) for charger, status in rows
                         if charger in connected and status in
                         {"Charging", "SuspendedEV", "SuspendedEVSE", "Preparing", "Finishing", "Reserved"}}))


def evaluate_preflight(data_dir: str | Path, *, force: bool = False) -> InstallPreflight:
    root = Path(data_dir).expanduser()
    schema = inspect_schema(root)

    if schema.exists and schema.version == 0:
        return InstallPreflight(False, (), (), "events database is unversioned")
    if schema.exists and schema.version is not None and schema.version > CURRENT_SCHEMA_VERSION:
        return InstallPreflight(
            False,
            (),
            (),
            f"events database schema {schema.version} is newer than supported {CURRENT_SCHEMA_VERSION}",
        )

    connected = _connected_chargers(root, schema.version)
    active_database = _live_active_chargers(root, connected) if schema.version is not None else ()
    if active_database and not force:
        return InstallPreflight(
            False,
            connected,
            active_database,
            "active charging detected; service handoff is blocked until transactions stop",
        )

    return InstallPreflight(True, connected, active_database, "charging safety override" if active_database else None)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only install safety preflight")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("-j", "--json", action="store_true")
    parser.add_argument("--force", action="store_true", help="Override charging activity guard only")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = evaluate_preflight(args.data_dir, force=args.force)
    if args.json:
        print(json.dumps(asdict(result), sort_keys=True))
    elif not result.allowed:
        print(f"Installation refused: {result.reason}")
        if result.connected_chargers:
            print("Connected charger(s): " + ", ".join(result.connected_chargers))
        if result.active_chargers:
            print("Active charger(s): " + ", ".join(result.active_chargers))
    return 0 if result.allowed else 1


if __name__ == "__main__":
    raise SystemExit(main())
