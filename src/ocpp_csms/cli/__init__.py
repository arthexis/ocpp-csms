"""Command-line interface for the OCPP CSMS appliance."""
from __future__ import annotations
import argparse
import sys
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
from ocpp_csms.cli.mail import add_mail_command, run_mail
from ocpp_csms.cli.report import add_report_command, run_report
from ocpp_csms.cli.reservation import add_reservation_commands, run_reservation
from ocpp_csms.cli.profile import add_profile_command, run_profile
from ocpp_csms.cli.rfid import add_rfid_command, run_rfid, run_rfid_action, run_rfid_edit
from ocpp_csms.cli.tls import add_tls_command, run_tls
from ocpp_csms.cli.txn_explain import run_txn_explain
from ocpp_csms.cli.transactions import TRANSACTION_COMMANDS, add_transaction_parser, build_txn_operations_parser, run_transactions
from ocpp_csms.transactions.archive import default_data_dir


def build_parser() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    parser = argparse.ArgumentParser(prog="ocpp-csms", description="Small OCPP 1.6J CSMS appliance.")
    parser.add_argument("--data-dir", default=str(default_data_dir()), help="Writable data directory (default: %(default)s")
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
    commands["mail"] = add_mail_command(subcommands)
    commands["report"] = add_report_command(subcommands)
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
    printed: set[int] = set()
    for command in commands.values():
        if id(command) in printed: continue
        printed.add(id(command)); print(); command.print_help()


def main() -> int:
    argv = list(sys.argv[1:])
    # Transaction operations use their own real subparsers; txn [ID] remains
    # the historical read-only query grammar without any argv rewriting.
    txn_offset = next((i for i, word in enumerate(argv) if word == "txn"), -1)
    if txn_offset >= 0 and len(argv) > txn_offset + 1 and argv[txn_offset + 1] in {"start", "stop", "explain"}:
        global_parser = argparse.ArgumentParser(add_help=False)
        global_parser.add_argument("--data-dir", default=str(default_data_dir()))
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
        if args.operation == "start" and args.charger and args.charger_option:
            operations.error("charge point may be specified positionally or with --cp, not both")
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
        if args.command in APPLIANCE_COMMANDS: return run_appliance(args)
        if args.command in CONTROL_COMMANDS: return run_control(args)
        if args.command in MAINTENANCE_COMMANDS: return run_maintenance(args)
        if args.command == 'inspect': return run_inspect(args)
        if args.command in ('capabilities', 'reconcile'): return run_inspection(args)
        if args.command in CHARGER_COMMANDS: return run_chargers(args)
        if args.command == "reserve": return run_reservation(args)
        if args.command == "data": return run_data(args)
        if args.command == "data-transfer": return run_data_transfer(args)
        if args.command == "firmware": return run_firmware(args)
        if args.command == "diagnose": return run_diagnostics(args)
        if args.command == "config": return run_config_diff(args) if is_config_diff(args) else run_config_download(args) if is_config_download(args) else run_configuration(args)
        if args.command == "tls": return run_tls(args)
        if args.command == "auth": return run_auth(args)
        if args.command == "mail": return run_mail(args)
        if args.command == "report": return run_report(args)
        if args.command == "profile": return run_profile(args)
        if args.command == "energy": return run_energy(args)
        if args.command == "export": return run_export(args)
        if args.command == "rfid":
            if args.rfid_command is None:
                commands["rfid"].print_help(); return 0
            if args.rfid_command == "edit": return run_rfid_edit(args)
            if args.rfid_command == "report":
                print(run_rfid(args)); return 0
            return run_rfid_action(args)
        if args.command in DIAGNOSTIC_COMMANDS: return run_diagnostic(args)
        if args.command == "txn":
            if args.json: return run_transactions_json(args)
            print(run_transactions(args)); return 0
    except ValueError as exc:
        parser.error(str(exc))
    return 2


__all__ = ["add_transaction_parser", "build_parser", "configuration_request", "control_request", "main", "print_help", "run_appliance", "run_config_download", "run_configuration", "run_control", "run_chargers", "run_diagnostic", "run_energy", "run_export", "run_profile", "run_rfid", "run_rfid_action", "run_transactions"]
