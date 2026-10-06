from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from ocpp_csms import app
from ocpp_csms.config_report import configuration_snapshot, format_configuration_snapshot
from ocpp_csms.control import send_control
from ocpp_csms.status import appliance_status
from ocpp_csms.transactions import default_data_dir


def _download_invocation(argv: list[str]) -> bool:
    try:
        index = argv.index("config")
    except ValueError:
        return False
    return index + 1 < len(argv) and argv[index + 1] == "download"


def _build_download_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ocpp-csms")
    parser.add_argument("--data-dir", default=str(default_data_dir()))
    subcommands = parser.add_subparsers(dest="command", required=True)
    config = subcommands.add_parser("config")
    config_commands = config.add_subparsers(dest="config_command", required=True)
    download = config_commands.add_parser("download", help="Download a charger configuration snapshot")
    download.add_argument("charger", nargs="?", help="Charge point ID (optional when exactly one charger is connected)")
    download.add_argument("--charger", dest="charger_option", help="Explicit charge point ID")
    download.add_argument("-f", "--force", action="store_true", help="Query configuration even with an active transaction")
    download.add_argument("--show-sensitive", action="store_true", help="Do not mask sensitive configuration values")
    download.add_argument("--json", action="store_true", help="Print the snapshot as JSON")
    download.add_argument("--output", help="Write the JSON snapshot to this file")
    return parser


def _requested_charger(args: argparse.Namespace) -> str | None:
    if args.charger and args.charger_option:
        raise ValueError("charger may be provided either positionally or with --charger, not both")
    return args.charger_option or args.charger


def _connected_charger(data_dir: str) -> str | None:
    status = appliance_status(data_dir)
    chargers = [item.charger_id for item in status.get("chargers", []) if item.connected]
    return chargers[0] if len(chargers) == 1 else None


def run_config_download(args: argparse.Namespace) -> int:
    try:
        charger = _requested_charger(args)
    except ValueError as exc:
        print(f"error: {exc}")
        return 2

    request: dict[str, object] = {"command": "config", "force": bool(args.force)}
    if charger is not None:
        request["charger"] = charger

    try:
        response = asyncio.run(send_control(args.data_dir, request))
    except (ConnectionError, FileNotFoundError, OSError, ValueError) as exc:
        print(f"error: control unavailable: {exc}")
        return 1

    error = response.get("error")
    if error:
        detail = response.get("detail") or response.get("charger")
        suffix = f": {detail}" if detail is not None else ""
        print(f"error: {error}{suffix}")
        return 1

    payload = response.get("response")
    if not isinstance(payload, dict):
        print("error: invalid configuration response")
        return 1

    if charger is None:
        charger = _connected_charger(args.data_dir)

    try:
        snapshot = configuration_snapshot(
            payload,
            charger=charger,
            show_sensitive=bool(args.show_sensitive),
        )
    except ValueError as exc:
        print(f"error: {exc}")
        return 1

    serialized = json.dumps(snapshot, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).expanduser().write_text(serialized, encoding="utf-8")
    if args.json:
        print(serialized, end="")
    else:
        print(format_configuration_snapshot(snapshot))
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if not _download_invocation(argv):
        return app.main()
    parser = _build_download_parser()
    args = parser.parse_args(argv)
    return run_config_download(args)


if __name__ == "__main__":
    raise SystemExit(main())
