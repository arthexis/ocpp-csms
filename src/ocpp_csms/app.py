"""Compatibility imports for the pre-package CLI module.

The executable CLI now lives entirely in :mod:`ocpp_csms.cli`.  This module is
kept temporarily so downstream imports can migrate without changing command
behavior; new code should import from ``ocpp_csms.cli`` or its command modules.
"""

from ocpp_csms.cli import build_parser, main, print_help
from ocpp_csms.cli.appliance import initialize_storage, run_server
from ocpp_csms.cli.config import configuration_request, run_configuration
from ocpp_csms.cli.control import control_request, run_control
from ocpp_csms.cli.profile import run_profile
from ocpp_csms.cli.transactions import run_transactions

__all__ = [
    "build_parser",
    "configuration_request",
    "control_request",
    "initialize_storage",
    "main",
    "print_help",
    "run_configuration",
    "run_control",
    "run_profile",
    "run_server",
    "run_transactions",
]


if __name__ == "__main__":
    raise SystemExit(main())
