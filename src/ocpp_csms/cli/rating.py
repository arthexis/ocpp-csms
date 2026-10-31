"""Nominal charger rating CLI. No OCPP message or service restart is sent."""
from __future__ import annotations

import argparse
import json

from ocpp_csms.charger_ratings import _positive_watts, load_ratings, save_ratings
from ocpp_csms.status import appliance_status


def add_rating_command(subcommands: argparse._SubParsersAction) -> argparse.ArgumentParser:
    parser = subcommands.add_parser("rating", help="Configure nominal charger/connector power (not a charging limit)")
    actions = parser.add_subparsers(dest="rating_action", required=True)
    for name in ("set", "clear", "show", "list"):
        action = actions.add_parser(name)
        if name != "list":
            action.add_argument("--cp", help="Charge point ID (optional if exactly one known)")
        if name in ("set", "clear", "show"):
            action.add_argument("--connector", help="Connector ID or 'all'; omit for charger default")
        if name == "set":
            action.add_argument("--power-kw", type=float, required=True)
        action.add_argument("--json", action="store_true", help="Output JSON")
    return parser


def _resolve_cp(args: argparse.Namespace) -> str:
    if args.cp:
        return args.cp
    chargers = appliance_status(args.data_dir).get("chargers", [])
    candidates = sorted({item.charger_id for item in chargers})
    if len(candidates) != 1:
        raise ValueError("Specify --cp unless exactly one charge point is known")
    return candidates[0]


def _connectors(args: argparse.Namespace, cp: str) -> list[int]:
    value = getattr(args, "connector", None)
    if value is None:
        return []
    if value == "all":
        chargers = appliance_status(args.data_dir).get("chargers", [])
        matches = [item for item in chargers if item.charger_id == cp]
        ids = sorted({c.connector_id for item in matches for c in item.connectors if c.connector_id > 0})
        if not ids:
            raise ValueError("No known physical connectors; specify an explicit --connector")
        return ids
    if not value.isdecimal() or int(value) < 1:
        raise ValueError("Connector must be a positive integer or 'all'; connector 0 is a charge-point aggregate")
    return [int(value)]


def run_rating(args: argparse.Namespace) -> int:
    rows = load_ratings(args.data_dir)
    if args.rating_action == "list":
        result = rows
    else:
        cp = _resolve_cp(args)
        connectors = _connectors(args, cp)
        if args.rating_action in {"set", "clear"}:
            config = rows.setdefault(cp, {})
            overrides = config.setdefault("connectors", {})
            if args.rating_action == "set":
                watts = _positive_watts(args.power_kw)
                if connectors:
                    for connector in connectors:
                        overrides[str(connector)] = watts
                else:
                    config["default_w"] = watts
            elif connectors:
                for connector in connectors:
                    overrides.pop(str(connector), None)
            else:
                config.pop("default_w", None)
            if not overrides:
                config.pop("connectors", None)
            if not config:
                rows.pop(cp, None)
            save_ratings(args.data_dir, rows)
        result = {cp: rows.get(cp, {})}
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        for cp, config in result.items():
            if not config:
                print(f"{cp}: no nominal rating configured")
                continue
            if config.get("default_w") is not None:
                print(f"{cp}: default {config['default_w'] / 1000:g} kW (charger-wide)")
            for connector, watts in sorted(config.get("connectors", {}).items(), key=lambda pair: int(pair[0])):
                print(f"{cp}: C{connector} {watts / 1000:g} kW (connector override)")
    return 0
