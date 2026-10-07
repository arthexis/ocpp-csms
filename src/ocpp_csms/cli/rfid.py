from __future__ import annotations

import argparse
from collections import defaultdict

from ocpp_csms.rfid_authorization import load_rfid_authorization
from ocpp_csms.transaction_cli import format_transactions, transaction_energy_wh
from ocpp_csms.transaction_query import TransactionQuery, TransactionView


def add_rfid_command(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    rfid = subcommands.add_parser("rfid", help="Inspect RFID activity")
    rfid_subcommands = rfid.add_subparsers(dest="rfid_command", required=True)
    report = rfid_subcommands.add_parser(
        "report",
        help="Report RFID transactions and energy, summarized when no tag is given",
    )
    report.add_argument("tag", nargs="?", help="OCPP RFID/idTag for detailed reporting")
    return rfid


def _format_energy(total_wh: int) -> str:
    return f"{total_wh / 1000:.3f} kWh" if total_wh >= 1000 else f"{total_wh} Wh"


def _energy_summary(views: list[TransactionView], *, compact: bool = False) -> str:
    energies = [transaction_energy_wh(view) for view in views]
    known = [energy for energy in energies if energy is not None]
    total = _format_energy(sum(known))
    if len(known) != len(views):
        if compact:
            total += f" ({len(known)}/{len(views)})"
        else:
            total += f" ({len(known)} of {len(views)} transactions)"
    return total


def _summary_table(data_dir: str) -> str:
    views = TransactionQuery(data_dir).list()
    grouped: dict[str, list[TransactionView]] = defaultdict(list)
    for view in views:
        if view.id_tag:
            grouped[view.id_tag].append(view)

    if not grouped:
        return "No RFID transactions."

    policy = load_rfid_authorization(data_dir)
    show_authorization = policy.source is not None

    headers = ["RFID", "TXNS", "ENERGY"]
    if show_authorization:
        headers.extend(("ALLOW", "NAME"))

    rows: list[tuple[str, ...]] = []
    for tag in sorted(grouped):
        tag_views = grouped[tag]
        values = [tag, str(len(tag_views)), _energy_summary(tag_views, compact=True)]
        if show_authorization:
            entry = policy.entries.get(tag) if policy.valid else None
            allowed = bool(entry and entry.enabled and policy.valid)
            values.extend(("true" if allowed else "false", entry.name if entry and entry.name else "-"))
        rows.append(tuple(values))

    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    ]

    def line(values: tuple[str, ...] | list[str]) -> str:
        return "  ".join(
            value.ljust(widths[index]) for index, value in enumerate(values)
        ).rstrip()

    return "\n".join([line(headers), *(line(row) for row in rows)])


def run_rfid(args: argparse.Namespace) -> str:
    if args.rfid_command != "report":
        raise ValueError(f"Unknown RFID command: {args.rfid_command}")

    if args.tag is None:
        return _summary_table(args.data_dir)

    views = TransactionQuery(args.data_dir).list(id_tag=args.tag)
    if not views:
        return f"RFID {args.tag}\n\nNo transactions.\n\nTransactions: 0\nEnergy:       0 Wh"

    return "\n".join(
        (
            f"RFID {args.tag}",
            "",
            format_transactions(views),
            "",
            f"Transactions: {len(views)}",
            f"Energy:       {_energy_summary(views)}",
        )
    )
