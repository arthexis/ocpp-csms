from __future__ import annotations

import argparse
import asyncio
import hashlib
from collections import defaultdict

from ocpp_csms.control import send_control
from ocpp_csms.rfid_authorization import load_rfid_authorization
from ocpp_csms.transaction_cli import format_transactions, transaction_energy_wh
from ocpp_csms.transaction_query import TransactionQuery, TransactionView


def add_rfid_command(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    rfid = subcommands.add_parser("rfid", help="Inspect and manage RFID authorization")
    rfid_subcommands = rfid.add_subparsers(dest="rfid_command", required=True)

    report = rfid_subcommands.add_parser(
        "report",
        help="Report RFID transactions and energy, summarized when no tag is given",
    )
    report.add_argument("tag", nargs="?", help="OCPP RFID/idTag for detailed reporting")

    for name, help_text in (
        ("export", "Send enabled rfid.csv entries to the charger's local authorization list"),
        ("version", "Read the charger's local authorization list version"),
        ("clear", "Clear the charger's local authorization list"),
    ):
        command = rfid_subcommands.add_parser(name, help=help_text)
        command.add_argument(
            "charger",
            nargs="?",
            help="Charge point ID (optional when exactly one charger is connected)",
        )
        command.add_argument("-c", "--charger", dest="charger_option", help="Explicit charge point ID")

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
            if not policy.valid:
                allow = "false"
            elif entry is None:
                allow = "missing"
            else:
                allow = "true" if entry.enabled else "false"
            values.extend((allow, entry.name if entry and entry.name else "-"))
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
        raise ValueError(f"RFID command {args.rfid_command} is not a report")

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


def _requested_charger(args: argparse.Namespace) -> str | None:
    positional = getattr(args, "charger", None)
    option = getattr(args, "charger_option", None)
    if positional and option:
        raise ValueError("charger may be provided either positionally or with --charger, not both")
    return option or positional


def _list_hash(entries: list[dict[str, object]]) -> str:
    payload = "\n".join(sorted(str(entry["rfid"]) for entry in entries))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _control_request(args: argparse.Namespace) -> dict[str, object]:
    charger = _requested_charger(args)
    request: dict[str, object] = {"command": f"rfid_{args.rfid_command}"}
    if charger is not None:
        request["charger"] = charger

    if args.rfid_command == "export":
        policy = load_rfid_authorization(args.data_dir)
        if policy.source is None:
            raise ValueError("rfid.csv is required for RFID export")
        if not policy.valid:
            raise ValueError(f"rfid.csv is invalid: {policy.error}")
        entries: list[dict[str, object]] = [
            {"rfid": entry.rfid, "name": entry.name, "enabled": True}
            for entry in policy.entries.values()
            if entry.enabled
        ]
        request.update(
            {
                "entries": entries,
                "source_file": policy.source.name,
                "list_hash": _list_hash(entries),
            }
        )
    return request


def _print_failure(command: str, response: dict[str, object]) -> int:
    error = response.get("error", "command_failed")
    detail = response.get("detail")
    suffix = f": {detail}" if detail else ""
    print(f"RFID {command} failed: {error}{suffix}")
    return 1


def run_rfid_action(args: argparse.Namespace) -> int:
    if args.rfid_command not in {"export", "version", "clear"}:
        raise ValueError(f"Unknown RFID action: {args.rfid_command}")

    request = _control_request(args)
    try:
        result = asyncio.run(send_control(args.data_dir, request))
    except (OSError, ValueError, ConnectionError) as exc:
        print(f"RFID {args.rfid_command} failed: {exc}")
        return 1

    if "error" in result:
        return _print_failure(args.rfid_command, result)

    response = result.get("response")
    if not isinstance(response, dict):
        print(f"RFID {args.rfid_command} failed: invalid control response")
        return 1

    if args.rfid_command == "version":
        version = response.get("list_version")
        if not isinstance(version, int):
            print("RFID version failed: charger returned no local-list version")
            return 1
        print(f"RFID local list version: {version}")
        return 0

    status = response.get("status")
    charger = response.get("charger", _requested_charger(args) or "-")
    list_version = response.get("list_version")
    verified = response.get("verified_version")
    cards = response.get("cards", 0)

    print(f"Charger:          {charger}")
    if args.rfid_command == "export":
        print("Source:           rfid.csv")
    print(f"Cards:            {cards}")
    print("Update:           Full")
    print(f"Previous version: {response.get('previous_version', '-')}")
    print(f"Sent version:     {list_version}")
    print(f"Result:           {status or '-'}")
    print(f"Verified:         {verified if verified is not None else '-'}")

    if status != "Accepted":
        return 1
    expected = 0 if args.rfid_command == "clear" else list_version
    if verified != expected:
        if response.get("verification_error"):
            print(f"Verification:     failed ({response['verification_error']})")
        else:
            print(f"Verification:     expected version {expected}")
        return 1
    return 0
