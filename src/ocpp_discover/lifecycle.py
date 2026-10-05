from __future__ import annotations

import argparse
from pathlib import Path

from ocpp_discover import handoff

DEFAULT_PERSISTENT_DIR = Path("/var/lib/ocpp-discover")
LEGACY_PERSISTENT_DIR = Path("/var/lib/ocpp-csms/discover")


def migrate_persistent_state(
    *,
    legacy_dir: str | Path = LEGACY_PERSISTENT_DIR,
    persistent_dir: str | Path = DEFAULT_PERSISTENT_DIR,
) -> str:
    """Migrate one validated durable adaptation without choosing between conflicts."""
    legacy_path = handoff.persistent_receipt_path(legacy_dir)
    persistent_path = handoff.persistent_receipt_path(persistent_dir)

    if legacy_path.exists() and persistent_path.exists():
        raise RuntimeError("conflicting_persistent_discover_state")
    if persistent_path.exists():
        handoff.load_persistent_path_a(persistent_dir)
        return "preserved"
    if not legacy_path.exists():
        return "absent"

    receipt = handoff.load_persistent_path_a(legacy_dir)
    handoff.persist_validated_path_a(persistent_dir, receipt)
    legacy_path.unlink()
    try:
        legacy_path.parent.rmdir()
    except OSError:
        pass
    return "migrated"


def remove_persistent_state(
    *,
    persistent_dir: str | Path = DEFAULT_PERSISTENT_DIR,
    legacy_dir: str | Path = LEGACY_PERSISTENT_DIR,
) -> tuple[Path, ...]:
    """Remove only Discover's known durable receipt files."""
    removed: list[Path] = []
    for root in (Path(persistent_dir).expanduser(), Path(legacy_dir).expanduser()):
        path = handoff.persistent_receipt_path(root)
        if path.exists():
            path.unlink()
            removed.append(path)
        try:
            root.rmdir()
        except OSError:
            pass
    return tuple(removed)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage OCPP Discover persistent state ownership.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("migrate", "remove"):
        command = subparsers.add_parser(name)
        command.add_argument("--persistent-dir", default=str(DEFAULT_PERSISTENT_DIR))
        command.add_argument("--legacy-dir", default=str(LEGACY_PERSISTENT_DIR))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "migrate":
            print(migrate_persistent_state(legacy_dir=args.legacy_dir, persistent_dir=args.persistent_dir))
        else:
            remove_persistent_state(persistent_dir=args.persistent_dir, legacy_dir=args.legacy_dir)
            print("persistent discovery state removed")
    except (RuntimeError, ValueError, OSError) as exc:
        print(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
