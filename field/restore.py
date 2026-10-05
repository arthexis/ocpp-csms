from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from field import handoff
from field import redirect as redirect_tools
from field.discover import host_addresses


def _interface_exists(interface: str) -> bool:
    result = subprocess.run(
        ["ip", "link", "show", "dev", interface],
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def restore_path_a(
    *,
    persistent_dir: str | Path,
    runtime_dir: str | Path,
    listen_port: int,
) -> str:
    """Restore only the exact previously validated Path A redirect."""
    if os.geteuid() != 0:
        raise RuntimeError("root_required")
    if not 1 <= listen_port <= 65535:
        raise ValueError("invalid_listen_port")

    receipt = handoff.load_persistent_path_a(persistent_dir)
    if receipt.listen_port != listen_port:
        raise RuntimeError("persistent_path_a_listener_mismatch")
    if not _interface_exists(receipt.interface):
        raise RuntimeError("persistent_path_a_interface_missing")

    destinations = receipt.destination_ips
    if len(destinations) != 1:
        raise RuntimeError("invalid_persistent_path_a_receipt")
    destination = destinations[0]
    if destination not in host_addresses():
        raise RuntimeError("persistent_path_a_destination_not_host_owned")
    if not redirect_tools.listener_available(receipt.listen_port):
        raise RuntimeError("target_listener_unavailable")
    if redirect_tools.table_exists():
        raise RuntimeError("redirect_table_exists")

    runtime_root = Path(runtime_dir).expanduser()
    runtime_root.mkdir(parents=True, exist_ok=True)
    runtime_receipt = runtime_root / "redirect.json"
    if runtime_receipt.exists():
        raise RuntimeError("redirect_receipt_exists")
    runtime_receipt.write_text(
        __import__("json").dumps(receipt.to_json(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    try:
        return redirect_tools.apply_redirect(runtime_root)
    except Exception:
        runtime_receipt.unlink(missing_ok=True)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m field.restore",
        description="Restore a previously validated persistent Path A redirect without discovery.",
    )
    parser.add_argument("--persistent-dir", default="/var/lib/ocpp-csms/discover")
    parser.add_argument("--runtime-dir", default="/run/ocpp-discover")
    parser.add_argument("--listen-port", type=int, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        ruleset = restore_path_a(
            persistent_dir=args.persistent_dir,
            runtime_dir=args.runtime_dir,
            listen_port=args.listen_port,
        )
    except (RuntimeError, ValueError, OSError) as exc:
        print(str(exc))
        return 1
    print(ruleset, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
