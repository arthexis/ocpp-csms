from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

from ocpp_csms.runtime import process_is_running
from ocpp_csms.schema import CURRENT_SCHEMA_VERSION, inspect_schema
from ocpp_csms.transaction_query import TransactionQuery


@dataclass(frozen=True)
class InstallPreflight:
    allowed: bool
    mode: str
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


def _archive_active_chargers(data_dir: Path) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                view.charge_point_id
                for view in TransactionQuery(data_dir).active()
                if view.charge_point_id
            }
        )
    )


def _database_active_chargers(data_dir: Path, schema_version: int | None) -> tuple[str, ...] | None:
    if schema_version is None or schema_version < 2:
        return None
    database = inspect_schema(data_dir).path
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        rows = connection.execute(
            "SELECT DISTINCT charger_id FROM transactions WHERE state IN ('open', 'recovered')"
        ).fetchall()
    return tuple(sorted(str(row[0]) for row in rows if row[0]))


def evaluate_preflight(data_dir: str | Path, *, rollover: bool = False) -> InstallPreflight:
    root = Path(data_dir).expanduser()
    schema = inspect_schema(root)

    if schema.exists and schema.version == 0:
        return InstallPreflight(False, "blocked", (), (), "events database is unversioned")
    if schema.exists and schema.version is not None and schema.version > CURRENT_SCHEMA_VERSION:
        return InstallPreflight(
            False,
            "blocked",
            (),
            (),
            f"events database schema {schema.version} is newer than supported {CURRENT_SCHEMA_VERSION}",
        )

    active_archive = _archive_active_chargers(root)
    active_database = _database_active_chargers(root, schema.version)
    connected = _connected_chargers(root, schema.version)

    if active_database is not None and active_database != active_archive:
        active = tuple(sorted(set(active_database) | set(active_archive)))
        return InstallPreflight(
            False,
            "blocked",
            connected,
            active,
            "active transaction state disagrees between SQLite and transaction archive",
        )

    if active_archive:
        return InstallPreflight(
            False,
            "blocked",
            connected,
            active_archive,
            "active charging detected; --rollover cannot override active charging",
        )

    if connected and not rollover:
        return InstallPreflight(
            False,
            "rollover-required",
            connected,
            (),
            "idle chargers are connected; re-run with --rollover to authorize handoff",
        )

    return InstallPreflight(True, "rollover" if connected else "normal", connected, ())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only install safety preflight")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--rollover", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = evaluate_preflight(args.data_dir, rollover=args.rollover)
    if args.json:
        print(json.dumps(asdict(result), sort_keys=True))
    elif result.allowed:
        if result.mode == "rollover":
            print("Rollover authorized for idle connected charger(s): " + ", ".join(result.connected_chargers))
    else:
        print(f"Installation refused: {result.reason}")
        if result.connected_chargers:
            print("Connected charger(s): " + ", ".join(result.connected_chargers))
        if result.active_chargers:
            print("Active charger(s): " + ", ".join(result.active_chargers))
    return 0 if result.allowed else 1


if __name__ == "__main__":
    raise SystemExit(main())
