from __future__ import annotations

import argparse
from pathlib import Path

from ocpp_discover import handoff, persistence

DEFAULT_PERSISTENT_DIR = Path("/var/lib/ocpp-discover")


def prepare_persistent_state(*, persistent_dir: str | Path = DEFAULT_PERSISTENT_DIR) -> str:
    """Validate existing Discover state; there is no production legacy migration."""
    path = handoff.persistent_receipt_path(persistent_dir)
    if not path.exists():
        return "absent"
    handoff.load_persistent_path_a(persistent_dir)
    return "preserved"


def remove_persistent_state(*, persistent_dir: str | Path = DEFAULT_PERSISTENT_DIR) -> tuple[Path, ...]:
    """Remove only Discover-owned durable state and persistent nftables integration."""
    removed: list[Path] = []
    root = Path(persistent_dir).expanduser()
    path = handoff.persistent_receipt_path(root)
    if path.exists():
        path.unlink()
        removed.append(path)
    try:
        root.rmdir()
    except OSError:
        pass

    ruleset = persistence.DEFAULT_RULESET_PATH
    if persistence.remove_ruleset(ruleset_path=ruleset):
        removed.append(ruleset)
    persistence.remove_nftables_include()
    return tuple(removed)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage OCPP Discover persistent state ownership.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--persistent-dir", default=str(DEFAULT_PERSISTENT_DIR))
    remove = subparsers.add_parser("remove")
    remove.add_argument("--persistent-dir", default=str(DEFAULT_PERSISTENT_DIR))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "prepare":
            print(prepare_persistent_state(persistent_dir=args.persistent_dir))
        else:
            remove_persistent_state(persistent_dir=args.persistent_dir)
            print("persistent discovery state removed")
    except (RuntimeError, ValueError, OSError) as exc:
        print(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
