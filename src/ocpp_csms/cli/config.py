from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from ocpp_csms.config_report import configuration_snapshot, format_configuration_snapshot
from ocpp_csms.control import send_control
from ocpp_csms.status import appliance_status


def add_config_download_arguments(config: argparse.ArgumentParser) -> None:
    """Add options used by the ``config download`` command.

    The existing config command still accepts free-form KEY arguments during the
    CLI migration, so ``download`` remains represented in ``items`` until the
    complete config family moves into this module.
    """
    config.add_argument("--show-sensitive", action="store_true", help="Do not mask sensitive configuration values")
    config.add_argument("--json", action="store_true", help="Print a downloaded snapshot as JSON")
    config.add_argument("--output", help="Write a downloaded JSON snapshot to this file")


def is_config_download(args: argparse.Namespace) -> bool:
    return bool(args.items) and args.items[0] == "download"


def _download_charger(args: argparse.Namespace) -> str | None:
    items = list(args.items)
    positional = items[1] if len(items) > 1 else None
    if len(items) > 2:
        raise ValueError("config download accepts at most one charger")
    option = args.charger
    if positional and option:
        raise ValueError("charger may be provided either positionally or with --charger, not both")
    return option or positional


def _connected_charger(data_dir: str) -> str | None:
    status = appliance_status(data_dir)
    chargers = [item.charger_id for item in status.get("chargers", []) if item.connected]
    return chargers[0] if len(chargers) == 1 else None


def run_config_download(args: argparse.Namespace) -> int:
    try:
        charger = _download_charger(args)
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
        snapshot = configuration_snapshot(payload, charger=charger, show_sensitive=bool(args.show_sensitive))
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
