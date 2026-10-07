from __future__ import annotations

import argparse
import sqlite3

from ocpp_csms.energy_contract import energy_contract
from ocpp_csms.energy_query import EnergyQuery
from ocpp_csms.output import emit_json


def add_energy_command(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> argparse.ArgumentParser:
    energy = subcommands.add_parser("energy", help="Show normalized charger energy telemetry")
    energy.add_argument("--cp", help="Filter by charge point ID")
    energy.add_argument("-c", "--connector", dest="connector", type=int, help="Filter by connector ID")
    energy.add_argument("--since", help="ISO-8601 lower sample timestamp bound")
    energy.add_argument("--until", help="ISO-8601 upper sample timestamp bound")
    energy.add_argument("-j", "--json", action="store_true", help="Print the stable machine-readable energy contract")
    return energy


def run_energy(args: argparse.Namespace) -> int:
    if args.connector is not None and args.connector < 0:
        raise ValueError("-c/--connector must be zero or greater")
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
