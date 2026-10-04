from __future__ import annotations

import argparse
import time
from pathlib import Path

from ocpp_csms.install_preflight import _connected_chargers
from ocpp_csms.schema import (
    CURRENT_SCHEMA_VERSION,
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


def wait_for_reconnect(
    data_dir: str | Path,
    expected: tuple[str, ...],
    *,
    timeout: float = 30.0,
    interval: float = 0.5,
) -> tuple[str, ...]:
    """Wait until every expected charger has a fresh connected state."""
    expected_set = set(expected)
    if not expected_set:
        return ()
    deadline = time.monotonic() + timeout
    while True:
        connected = set(_connected_chargers(Path(data_dir).expanduser(), CURRENT_SCHEMA_VERSION))
        missing = tuple(sorted(expected_set - connected))
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

    reconnect = subcommands.add_parser("wait-reconnect")
    reconnect.add_argument("--data-dir", required=True)
    reconnect.add_argument("--charger", action="append", default=[])
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
        missing = wait_for_reconnect(
            args.data_dir,
            tuple(args.charger),
            timeout=args.timeout,
        )
        if missing:
            print("Missing charger(s): " + ", ".join(missing))
            return 1
        return 0
    except RuntimeError as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
