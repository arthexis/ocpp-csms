from __future__ import annotations

import argparse

from ocpp_csms.export_contract import export_contract
from ocpp_csms.output import emit_json


def add_export_command(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    export = subcommands.add_parser("export", help="Export cursor-addressed OCPP data for forwarding")
    export.add_argument("--after", type=int, default=0, help="Export OCPP events after this source cursor")
    export.add_argument("--limit", type=int, default=500, help="Maximum OCPP events in one page")
    export.add_argument("-j", "--json", action="store_true", help="Print the stable machine-readable export contract")
    return export


def run_export(args: argparse.Namespace) -> int:
    payload = export_contract(args.data_dir, after=args.after, limit=args.limit)
    if args.json:
        emit_json(payload)
        return 0

    data = payload["data"]
    cursor = data["cursor"]
    print(
        f"Source: {data['source_id']}\n"
        f"Events: {len(data['events'])}\n"
        f"Cursor: {cursor['after']} -> {cursor['next']}\n"
        f"More: {'yes' if cursor['more'] else 'no'}"
    )
    return 0
