"""Safe opt-in archival of isolated CSMS test data (never removes configuration)."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

from ocpp_csms.install_preflight import evaluate_preflight
from ocpp_csms.runtime import process_is_running
from ocpp_csms.cli.appliance import initialize_storage


def add_data_command(subcommands: argparse._SubParsersAction) -> argparse.ArgumentParser:
    data = subcommands.add_parser("data", help="Inspect or archive appliance test data")
    operations = data.add_subparsers(dest="data_command", required=True)
    reset = operations.add_parser("reset", help="Archive all test data and initialize clean storage")
    reset.add_argument("--test-only", action="store_true", help="Confirm data are disposable simulator/test data only")
    mode = reset.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Check safety without modifying files (default)")
    mode.add_argument("--now", action="store_true", help="Actually archive test data and initialize an empty store")
    return data


def reset_test_data(data_dir: str | Path, *, test_only: bool, now: bool = False) -> dict:
    if not test_only:
        raise ValueError("reset requires --test-only affirmation (no production data)")
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
    preflight = evaluate_preflight(root)
    if not preflight.allowed or preflight.connected_chargers or preflight.active_chargers:
        raise ValueError("data reset refused: " + (preflight.reason or "connected/active charger evidence"))
    if root.resolve() in (Path.home().resolve(), Path("/")):
        raise ValueError("refusing reset of home or filesystem root")
    backup_root = root.parent / (root.name + "-test-backups")
    if backup_root.is_symlink():
        raise ValueError("refusing symlinked backup directory")
    if not now:
        return {"decision": "dry-run", "data_dir": str(root), "backup_parent": str(backup_root),
                "preserve": "deployment profile, nginx/TLS and Basic authentication configuration"}
    backup_root.mkdir(mode=0o700, exist_ok=True)
    os.chmod(backup_root, 0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = backup_root / stamp
    if backup.exists():
        raise ValueError("backup destination already exists")
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
    return {"decision": "reset", "backup": str(backup), "data_dir": str(root),
            "preserve": "deployment profile, nginx/TLS and Basic authentication configuration"}


def run_data(args: argparse.Namespace) -> int:
    if args.data_command != "reset":
        raise ValueError("unknown data operation")
    print(json.dumps(reset_test_data(args.data_dir, test_only=args.test_only, now=args.now),
                     indent=2, sort_keys=True))
    return 0
