from __future__ import annotations

import argparse

from ocpp_csms.transaction_cli import format_transactions, transaction_energy_wh
from ocpp_csms.transaction_query import TransactionQuery


def add_rfid_command(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    rfid = subcommands.add_parser("rfid", help="Inspect RFID activity")
    rfid_subcommands = rfid.add_subparsers(dest="rfid_command", required=True)
    report = rfid_subcommands.add_parser("report", help="Report transactions and energy for an RFID tag")
    report.add_argument("tag", help="OCPP RFID/idTag to report")
    return rfid


def run_rfid(args: argparse.Namespace) -> str:
    if args.rfid_command != "report":
        raise ValueError(f"Unknown RFID command: {args.rfid_command}")

    views = TransactionQuery(args.data_dir).list(id_tag=args.tag)
    if not views:
        return f"RFID {args.tag}\n\nNo transactions.\n\nTransactions: 0\nEnergy:       0 Wh"

    energies = [transaction_energy_wh(view) for view in views]
    known = [energy for energy in energies if energy is not None]
    total_wh = sum(known)
    total = f"{total_wh / 1000:.3f} kWh" if total_wh >= 1000 else f"{total_wh} Wh"

    energy_line = f"Energy:       {total}"
    if len(known) != len(views):
        energy_line += f" ({len(known)} of {len(views)} transactions)"

    return "\n".join(
        (
            f"RFID {args.tag}",
            "",
            format_transactions(views),
            "",
            f"Transactions: {len(views)}",
            energy_line,
        )
    )
