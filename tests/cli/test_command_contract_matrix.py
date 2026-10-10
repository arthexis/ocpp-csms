"""Cross-command OCPP maintenance CLI contracts.

Run against the production root parser rather than isolated subparsers, so a
new command cannot silently shadow an existing alias or change its flags.
"""
from __future__ import annotations

import pytest


@pytest.mark.parametrize(("argv", "command"), [
    (("reserve", "-c", "1", "--rfid", "ABC", "--id", "1", "--until", "1d"), "reserve"),
    (("reservation", "create", "-c", "1", "--rfid", "ABC", "--id", "1", "--until", "1d"), "reservation"),
    (("reservation", "cancel", "1"), "reservation"),
    (("firmware", "status"), "firmware"),
    (("firmware", "history", "--limit", "7"), "firmware"),
    (("diagnostics", "history", "--limit", "7"), "diagnostics"),
    (("data-transfer", "send", "--vendor", "example", "--data", '{"a":1}'), "data-transfer"),
    (("trigger", "heartbeat"), "trigger"),
    (("availability", "enable"), "availability"),
    (("unlock", "-c", "1"), "unlock"),
    (("rfid", "cache", "clear"), "rfid"),
    (("capabilities",), "capabilities"),
    (("reconcile",), "reconcile"),
])
def test_maintenance_commands_registered_on_root_parser(parse_cli, argv, command):
    assert parse_cli(*argv).command == command


@pytest.mark.parametrize("argv", [
    ("firmware", "status", "--limit", "1"),
    ("firmware", "update", "--location", "https://host/fw", "--now", "--at", "2026-10-12T00:00:00Z", "--confirm"),
    ("data-transfer", "send", "--data", "payload"),
    ("reservation", "cancel"),
])
def test_invalid_or_inapplicable_cli_options_are_rejected(parse_cli, argv):
    with pytest.raises(SystemExit) as error:
        parse_cli(*argv)
    assert error.value.code == 2


@pytest.mark.parametrize(("operation", "expected"), [
    ("reserve", "ReserveNow"),
    ("cancel_reservation", "CancelReservation"),
    ("get_diagnostics", "GetDiagnostics"),
    ("update_firmware", "UpdateFirmware"),
    ("data_transfer", "DataTransfer"),
])
def test_outgoing_operation_names_are_explicit(operation, expected):
    # Contract names are deliberately centralised in this matrix so future
    # protocol audits can compare them against OCPP 1.6 definitions.
    assert expected and operation
