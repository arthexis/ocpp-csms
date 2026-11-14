"""Command-line interface for the OCPP CSMS appliance."""
from __future__ import annotations
import argparse
import shlex
import sys
from pathlib import Path
from ocpp_csms.cli.auth import add_auth_command, run_auth
from ocpp_csms.cli.appliance import APPLIANCE_COMMANDS, add_appliance_commands, run_appliance
from ocpp_csms.cli.chargers import CHARGER_COMMANDS, add_charger_commands, run_chargers
from ocpp_csms.cli.config import add_config_command, configuration_request, is_config_download, is_config_diff, run_config_download, run_configuration
from ocpp_csms.cli.config_diff import run_config_diff
from ocpp_csms.cli.control import CONTROL_COMMANDS, add_control_commands, control_request, run_control
from ocpp_csms.cli.charger_diagnostics import add_diagnostics_command, run_diagnostics
from ocpp_csms.cli.data import add_data_command, run_data
from ocpp_csms.cli.data_transfer import add_data_transfer_command, run_data_transfer
from ocpp_csms.cli.diagnostics import DIAGNOSTIC_COMMANDS, add_diagnostic_commands, run_diagnostic
from ocpp_csms.cli.energy import add_energy_command, run_energy
from ocpp_csms.cli.firmware import add_firmware_command, run_firmware
from ocpp_csms.cli.export import add_export_command, run_export
from ocpp_csms.cli.inspect import add_inspect_command, run_inspect
from ocpp_csms.cli.inspection import add_inspection_commands, run_inspection
from ocpp_csms.cli.json_contracts import run_transactions_json
from ocpp_csms.cli.maintenance import COMMANDS as MAINTENANCE_COMMANDS, add_maintenance_commands, run_maintenance
from ocpp_csms.cli.report import add_report_command, run_report
from ocpp_csms.cli.reservation import add_reservation_commands, run_reservation
from ocpp_csms.cli.profile import add_profile_command, run_profile
from ocpp_csms.cli.rfid import add_rfid_command, run_rfid, run_rfid_action, run_rfid_edit
from ocpp_csms.cli.rating import add_rating_command, run_rating
from ocpp_csms.transaction_soc import transaction_soc
from ocpp_csms.output import emit_json
from ocpp_csms.cli.tls import add_tls_command, run_tls
from ocpp_csms.cli.txn_explain import run_txn_explain
from ocpp_csms.cli.transactions import TRANSACTION_COMMANDS, add_transaction_parser, build_txn_operations_parser, run_transactions
from ocpp_csms.transactions.archive import default_data_dir


def cli_data_dir() -> str:
    """Read the installed service selection, even while the service is stopped."""
    unit = Path("/etc/systemd/system/ocpp-csms.service")
    try:
        lines = unit.read_text(encoding="utf-8").splitlines()
    except OSError:
        return str(default_data_dir())
    for line in lines:
        if not line.startswith("ExecStart="):
            continue
        try:
            argv = shlex.split(line.partition("=")[2])
            index = argv.index("--data-dir")
            selected = Path(argv[index + 1]).expanduser()
            if selected.is_absolute():
                return str(selected)
        except (ValueError, IndexError):
            pass
    return str(default_data_dir())


def build_parser() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    parser = argparse.ArgumentParser(prog="ocpp-csms", description="Small OCPP 1.6J CSMS appliance.")
    parser.add_argument("--data-dir", default=cli_data_dir(), help="Writable data directory (default: %(default)s")
    subcommands = parser.add_subparsers(dest="command")
    commands: dict[str, argparse.ArgumentParser] = {}
    commands.update(add_appliance_commands(subcommands))
    commands.update(add_control_commands(subcommands))
    commands.update(add_maintenance_commands(subcommands))
    commands.update(add_inspection_commands(subcommands))
    commands['inspect'] = add_inspect_command(subcommands)
    commands.update(add_charger_commands(subcommands))
    commands.update(add_reservation_commands(subcommands))
    commands["data"] = add_data_command(subcommands)
    commands["data-transfer"] = add_data_transfer_command(subcommands)
    commands["firmware"] = add_firmware_command(subcommands)
    commands["diagnose"] = add_diagnostics_command(subcommands)
    commands["config"] = add_config_command(subcommands)
    commands["profile"] = add_profile_command(subcommands)
    commands["energy"] = add_energy_command(subcommands)
    commands["export"] = add_export_command(subcommands)
    commands["rfid"] = add_rfid_command(subcommands)
    commands["tls"] = add_tls_command(subcommands)
    commands["auth"] = add_auth_command(subcommands)
    commands["report"] = add_report_command(subcommands)
    commands["rating"] = add_rating_command(subcommands)
    soc = subcommands.add_parser("transaction-soc", help="Read bounded historical SoC metadata without replaying events")
    soc.add_argument("--cp", required=True, help="Exact charge point identity")
    soc.add_argument("--transaction", type=int, required=True, help="Positive transaction ID")
    commands["transaction-soc"] = soc
    commands.update(add_diagnostic_commands(subcommands))
    transactions = add_transaction_parser(subcommands)
    transactions.add_argument("-j", "--json", action="store_true", help="Print the stable machine-readable transaction contract")
    transactions.epilog = "Operations: txn start, txn stop ID, txn explain ID. Use txn OP --help for details."
    for name in TRANSACTION_COMMANDS:
        commands[name] = transactions
    help_parser = subcommands.add_parser("help", help="Show commands and parameters")
    help_parser.add_argument("topic", nargs="?", choices=tuple(commands))
    return parser, commands


def print_help(parser, commands, topic=None) -> None:
    if topic:
        commands[topic].print_help(); return
    parser.print_help()



def _run_parsed_config(args: argparse.Namespace) -> int:
    # Keep the historic short-circuit priority: diff, download, then configure.
    return run_config_diff(args) if is_config_diff(args) else run_config_download(args) if is_config_download(args) else run_configuration(args)


def _run_parsed_transaction_soc(args: argparse.Namespace) -> int:
    emit_json(transaction_soc(args.data_dir, cp=args.cp, transaction=args.transaction))
    return 0


def _run_parsed_rfid(args: argparse.Namespace, commands: dict[str, argparse.ArgumentParser]) -> int:
    if args.rfid_command is None:
        commands["rfid"].print_help()
        return 0
    if args.rfid_command == "edit":
        return run_rfid_edit(args)
    if args.rfid_command == "report":
        print(run_rfid(args))
        return 0
    return run_rfid_action(args)


def _run_parsed_txn(args: argparse.Namespace) -> int:
    if args.json:
        return run_transactions_json(args)
    print(run_transactions(args))
    return 0


def _dispatch_parsed_command(args: argparse.Namespace, commands: dict[str, argparse.ArgumentParser]) -> int:
    """Route parsed root commands in their original priority order.

    Bind exported handlers on each call, not at import time: external callers
    and existing tests monkeypatch functions in ocpp_csms.cli directly.
    """
    routes = (
        (APPLIANCE_COMMANDS, run_appliance),
        (CONTROL_COMMANDS, run_control),
        (MAINTENANCE_COMMANDS, run_maintenance),
        (("inspect",), run_inspect),
        (("capabilities", "reconcile"), run_inspection),
        (CHARGER_COMMANDS, run_chargers),
        (("reserve",), run_reservation),
        (("data",), run_data),
        (("data-transfer",), run_data_transfer),
        (("firmware",), run_firmware),
        (("diagnose",), run_diagnostics),
        (("config",), _run_parsed_config),
        (("tls",), run_tls),
        (("auth",), run_auth),
        (("report",), run_report),
        (("rating",), run_rating),
        (("transaction-soc",), _run_parsed_transaction_soc),
        (("profile",), run_profile),
        (("energy",), run_energy),
        (("export",), run_export),
        (("rfid",), lambda parsed: _run_parsed_rfid(parsed, commands)),
        (DIAGNOSTIC_COMMANDS, run_diagnostic),
        (("txn",), _run_parsed_txn),
    )
    for names, handler in routes:
        if args.command in names:
            return handler(args)
    return 2


def main() -> int:
    argv = list(sys.argv[1:])
    # Transaction operations use their own real subparsers; txn [ID] remains
    # the historical read-only query grammar without any argv rewriting.
    txn_offset = next((i for i, word in enumerate(argv) if word == "txn"), -1)
    if txn_offset >= 0 and len(argv) > txn_offset + 1 and argv[txn_offset + 1] in {"start", "stop", "explain"}:
        global_parser = argparse.ArgumentParser(add_help=False)
        global_parser.add_argument("--data-dir", default=cli_data_dir())
        global_args = global_parser.parse_args(argv[:txn_offset])
        operations = build_txn_operations_parser()
        args = operations.parse_args(argv[txn_offset + 1:])
        args.data_dir = global_args.data_dir
        if args.operation == "explain":
            try:
                return run_txn_explain(args)
            except ValueError as exc:
                operations.error(str(exc))
        args.command = args.operation
        if args.operation == "stop":
            args.transaction = args.transaction_id
            if args.transaction is None:
                operations.error("txn stop requires a transaction ID")
            args.charger = None
        try:
            return run_control(args)
        except ValueError as exc:
            operations.error(str(exc))
    # argparse cannot combine an optional numeric ID with named subcommands in
    # one positional slot. Extract only the numeric detail selector before parse.
    reserve_offset = next((i for i, word in enumerate(argv) if word == "reserve"), -1)
    reserve_id = None
    if reserve_offset >= 0 and len(argv) > reserve_offset + 1:
        selector = argv[reserve_offset + 1]
        if selector.lstrip("-").isdecimal():
            reserve_id = int(argv.pop(reserve_offset + 1))
    parser, commands = build_parser(); args = parser.parse_args(argv)
    if reserve_id is not None:
        args.reservation_id = reserve_id
    if args.command is None or args.command == "help": print_help(parser, commands, getattr(args, "topic", None)); return 0
    try:
        return _dispatch_parsed_command(args, commands)
    except ValueError as exc:
        parser.error(str(exc))
    return 2


__all__ = ["add_transaction_parser", "build_parser", "configuration_request", "control_request", "main", "print_help", "run_appliance", "run_config_download", "run_configuration", "run_control", "run_chargers", "run_diagnostic", "run_energy", "run_export", "run_profile", "run_rfid", "run_rfid_action", "run_transactions"]
