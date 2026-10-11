from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from ocpp_csms.config_report import configuration_snapshot, format_configuration_snapshot
from ocpp_csms.control import send_control
from ocpp_csms.output import emit_json
from ocpp_csms.status import appliance_status


def add_config_command(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    """Register the configuration command while preserving its existing syntax."""
    config = subcommands.add_parser("config", help="Read or change charger configuration")
    config.add_argument("--cp", "--charger", dest="charger", help="Explicit charge point ID when more than one charger is connected")
    config.add_argument("items", nargs="*", metavar="KEY", help="Keys to read, set KEY VALUE, download, or diff FILE [FILE]")
    config.add_argument("-f", "--force", action="store_true", help="Operate even with an active transaction")
    add_config_download_arguments(config)
    return config


def add_config_download_arguments(config: argparse.ArgumentParser) -> None:
    """Add options used by the ``config download`` command."""
    config.add_argument("--show-sensitive", action="store_true", help="Do not mask sensitive configuration values")
    config.add_argument("-j", "--json", action="store_true", help="Print a downloaded snapshot as JSON")
    config.add_argument("--output", help="Write a downloaded JSON snapshot to this file")


def is_config_diff(args: argparse.Namespace) -> bool:
    return bool(args.items) and args.items[0] == 'diff'


def is_config_download(args: argparse.Namespace) -> bool:
    return bool(args.items) and args.items[0] == "download"


def configuration_request(args: argparse.Namespace) -> dict[str, object]:
    items = list(args.items)
    if items and items[0] == "set":
        if len(items) != 3:
            raise ValueError("config set requires KEY and VALUE")
        request: dict[str, object] = {
            "command": "config_set",
            "key": items[1],
            "value": items[2],
            "force": bool(args.force),
        }
    else:
        request = {"command": "config", "force": bool(args.force)}
        if items:
            request["keys"] = items
    if args.charger is not None:
        request["charger"] = args.charger
    return request


def _format_configuration(payload: dict[str, object]) -> str:
    rows = payload.get("configuration_key") or []
    unknown = payload.get("unknown_key") or []
    if not isinstance(rows, list) or not isinstance(unknown, list):
        raise ValueError("invalid configuration response")
    lines = ["KEY\tACCESS\tVALUE"]
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid configuration response")
        key = row.get("key")
        readonly = row.get("readonly")
        value = row.get("value")
        if not isinstance(key, str) or not isinstance(readonly, bool) or (value is not None and not isinstance(value, str)):
            raise ValueError("invalid configuration response")
        lines.append(f"{key}\t{'R' if readonly else 'RW'}\t{value or ''}")
    for key in unknown:
        if not isinstance(key, str):
            raise ValueError("invalid configuration response")
        lines.append(f"Unknown: {key}")
    return "\n".join(lines)


def run_configuration(args: argparse.Namespace) -> int:
    try:
        request = configuration_request(args)
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
        return 1
    if request["command"] == "config_set":
        change = payload.get("change")
        readback = payload.get("readback")
        if not isinstance(change, dict) or not isinstance(readback, dict):
            return 1
        status = change.get("status")
        print(status or "Unknown")
        try:
            print(_format_configuration(readback))
        except ValueError:
            return 1
        return 0 if status in {"Accepted", "RebootRequired"} else 1
    try:
        print(_format_configuration(payload))
    except ValueError:
        return 1
    return 0


def _download_charger(args: argparse.Namespace) -> str | None:
    items = list(args.items)
    positional = items[1] if len(items) > 1 else None
    if len(items) > 2:
        raise ValueError("config download accepts at most one charger")
    option = args.charger
    if positional and option:
        raise ValueError("charger may be provided either positionally or with --cp, not both")
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

    serialized = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    if args.output:
        Path(args.output).expanduser().write_text(serialized, encoding="utf-8")
    if args.json:
        emit_json(snapshot)
    else:
        print(format_configuration_snapshot(snapshot))
    return 0
