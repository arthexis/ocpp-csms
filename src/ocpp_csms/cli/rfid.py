from __future__ import annotations

import argparse
import asyncio
import hashlib
from collections import defaultdict

from ocpp_csms.control import send_control
from ocpp_csms.rfid.authorization import load_rfid_authorization
from ocpp_csms.rfid.cache import RFIDCacheState, resolve_rfid_cache_sync
from ocpp_csms.transactions.formatting import format_transactions, transaction_energy_wh
from ocpp_csms.transactions.query import TransactionQuery, TransactionView


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
        command.add_argument("--cp", "--charger", dest="charger_option", help="Explicit charge point ID")

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


def _entry_value(entry: object | None) -> str:
    if entry is None:
        return "missing"
    return "true" if bool(getattr(entry, "enabled", False)) else "false"


def _cache_entries(cache: RFIDCacheState | None) -> dict[str, object] | None:
    if cache is None or not cache.has_history:
        return None
    if not cache.known or cache.snapshot is None:
        return {}
    return {entry.rfid: entry for entry in cache.snapshot.entries}


def _summary_table(data_dir: str) -> str:
    views = TransactionQuery(data_dir).list()
    grouped: dict[str, list[TransactionView]] = defaultdict(list)
    for view in views:
        if view.id_tag:
            grouped[view.id_tag].append(view)

    if not grouped:
        return "No RFID transactions."

    policy = load_rfid_authorization(data_dir)
    cache = resolve_rfid_cache_sync(data_dir)
    cache_entries = _cache_entries(cache)

    file_configured = policy.source is not None
    cache_available = cache_entries is not None
    show_allow = file_configured or cache_available
    show_cache = file_configured and cache_available
    show_name = show_allow

    headers = ["RFID", "TXNS", "ENERGY"]
    if show_allow:
        headers.append("ALLOW")
    if show_cache:
        headers.append("CACHE")
    if show_name:
        headers.append("NAME")

    rows: list[tuple[str, ...]] = []
    for tag in sorted(grouped):
        tag_views = grouped[tag]
        values = [tag, str(len(tag_views)), _energy_summary(tag_views, compact=True)]

        file_entry = policy.entries.get(tag) if policy.valid else None
        cache_entry = cache_entries.get(tag) if cache_entries else None
        cache_unknown = cache_available and cache is not None and not cache.known

        if show_allow:
            if file_configured:
                if not policy.valid:
                    allow = "false"
                else:
                    allow = _entry_value(file_entry)
            elif cache_unknown:
                allow = "unknown"
            else:
                allow = _entry_value(cache_entry)
            values.append(allow)

        if show_cache:
            values.append("unknown" if cache_unknown else _entry_value(cache_entry))

        if show_name:
            if file_configured:
                name = file_entry.name if file_entry and file_entry.name else "-"
            elif cache_unknown:
                name = "-"
            else:
                name = getattr(cache_entry, "name", None) or "-"
            values.append(str(name))

        rows.append(tuple(values))

    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    ]

    def line(values: tuple[str, ...] | list[str]) -> str:
        return "  ".join(
            value.ljust(widths[index]) for index, value in enumerate(values)
        ).rstrip()

    output = [line(headers), *(line(row) for row in rows)]

    if cache_available and cache is not None:
        output.extend(("", f"Charger local list: version {cache.list_version}"))
        if file_configured:
            if not policy.valid or not cache.known or cache.snapshot is None:
                sync = "unknown"
            else:
                enabled_entries: list[dict[str, object]] = [
                    {"rfid": entry.rfid, "name": entry.name, "enabled": True}
                    for entry in policy.entries.values()
                    if entry.enabled
                ]
                sync = "current" if _list_hash(enabled_entries) == cache.snapshot.list_hash else "differs"
            output.append(f"Sync:          {sync}")

    return "\n".join(output)


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
        raise ValueError("charger may be provided either positionally or with --cp, not both")
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


_RFID_CONFIG_LABELS = (
    ("LocalAuthListEnabled", "Charger local list"),
    ("AuthorizationCacheEnabled", "Auth cache"),
    ("LocalAuthorizeOffline", "Offline auth"),
    ("LocalPreAuthorize", "Local preauth"),
    ("AllowOfflineTxForUnknownId", "Unknown offline"),
    ("StopTransactionOnInvalidId", "Stop invalid ID"),
    ("MaxEnergyOnInvalidId", "Invalid ID energy"),
    ("AuthorizeRemoteTxRequests", "Remote TX auth"),
    ("SupportedFeatureProfiles", "Feature profiles"),
    ("LocalAuthListMaxLength", "Local list capacity"),
    ("SendLocalListMaxLength", "Send list capacity"),
)


def _configuration_values(payload: object) -> tuple[dict[str, str], set[str], str | None]:
    if not isinstance(payload, dict):
        return {}, set(), "unavailable"
    error = payload.get("error")
    if error is not None:
        return {}, set(), str(error)
    rows = payload.get("configuration_key")
    unknown = payload.get("unknown_key")
    values: dict[str, str] = {}
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("key"), str):
                value = row.get("value")
                values[row["key"]] = str(value) if value is not None else ""
    unknown_keys = {key for key in unknown or [] if isinstance(key, str)} if isinstance(unknown, list) else set()
    return values, unknown_keys, None


def _print_rfid_configuration(payload: object) -> None:
    values, unknown, error = _configuration_values(payload)
    print("")
    print("RFID configuration:")
    if error is not None:
        print(f"  unavailable: {error}")
        return
    for key, label in _RFID_CONFIG_LABELS:
        value = values.get(key, "unknown" if key in unknown else "not reported")
        print(f"  {label + ':':21} {value}")
    if values.get("LocalAuthListEnabled", "").lower() == "false":
        print("")
        print("Warning: charger local list is disabled; stored list changes will not affect authorization.")


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
        print(f"RFID charger local list version: {version}")
        _print_rfid_configuration(response.get("configuration"))
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
    _print_rfid_configuration(response.get("configuration"))

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
