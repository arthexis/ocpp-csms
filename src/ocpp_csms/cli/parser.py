from __future__ import annotations

from ocpp_csms.cli.appliance import add_appliance_commands
from ocpp_csms.cli.config import add_config_command
from ocpp_csms.cli.control import add_control_commands
from ocpp_csms.cli.diagnostics import add_diagnostic_commands
from ocpp_csms.cli.profile import add_profile_command



__all__ = [
    "add_appliance_commands",
    "add_config_command",
    "add_control_commands",
    "add_diagnostic_commands",
    "add_profile_command",
]
