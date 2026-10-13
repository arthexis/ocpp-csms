from __future__ import annotations

import argparse
import sqlite3

from ocpp_csms.energy_contract import energy_contract
from ocpp_csms.cli.options import add_cp, add_connector, add_json, add_time_bounds, require_nonnegative
from ocpp_csms.energy_query import EnergyQuery
from ocpp_csms.output import emit_json


def add_energy_command(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> argparse.ArgumentParser:
    energy = subcommands.add_parser("energy", help="Show normalized charger energy telemetry")
    add_cp(energy, help="Filter by charge point ID")
    add_connector(energy, help="Filter by connector ID")
    add_time_bounds(energy, since_help="ISO-8601 lower sample timestamp bound", until_help="ISO-8601 upper sample timestamp bound")
    add_json(energy, help="Print the stable machine-readable energy contract")
    return energy


def run_energy(args: argparse.Namespace) -> int:
    require_nonnegative(args.connector, name="-c/--connector")
    query = EnergyQuery(args.data_dir)
    try:
        samples = query.samples(charger=args.charger, connector=args.connector, since=args.since, until=args.until)
        transactions, completed_energy_wh = query.completed_summary(charger=args.charger, connector=args.connector, since=args.since, until=args.until)
    except sqlite3.Error as exc:
        raise ValueError(str(exc)) from exc
    if args.json:
        emit_json(energy_contract(samples, completed_transactions=transactions, completed_energy_wh=completed_energy_wh))
        return 0
    print(f"Completed energy: {completed_energy_wh} Wh")
    print(f"Completed transactions: {transactions}")
    print(f"Telemetry samples: {len(samples)}")
    if samples:
        print(f"First sample: {samples[0].at}")
        print(f"Last sample: {samples[-1].at}")
    return 0
