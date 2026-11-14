"""Stable control dispatch facade: resolution, family routing and error boundary."""
from __future__ import annotations

from typing import Any
from .protocols import Session, SessionRegistry
from ._dispatch_validation import _INVALID, _configuration_keys, _non_negative_int, _positive_int, _rfid_entries
from ._dispatch_support import (_response_payload, _resolve_charger, _smart_charging_unsupported, _composite_schedule_response, _list_version, RFID_CONFIGURATION_KEYS, _rfid_configuration, _send_rfid_list)
from ._dispatch_data import dispatch_data
from ._dispatch_operations import dispatch_operations
from ._dispatch_rfid import dispatch_rfid
from ._dispatch_smart import dispatch_smart

_DATA_COMMANDS = frozenset(("data_transfer", "reserve", "cancel_reservation", "update_firmware", "get_diagnostics"))
_OPERATIONS_COMMANDS = frozenset(("rfid_cache_clear", "trigger", "availability", "unlock", "start", "stop", "reset", "config", "config_set"))
_RFID_COMMANDS = frozenset(("rfid_version", "rfid_export", "rfid_clear"))
_SMART_COMMANDS = frozenset(("set_charging_profile", "clear_charging_profile", "get_composite_schedule"))

async def dispatch_control(registry: SessionRegistry, request: dict[str, Any]) -> dict[str, Any]:
    command = request.get("command")
    if not isinstance(command, str) or not command:
        return {"error": "missing_command"}
    if command in {"tls_status", "tls_enable", "tls_reload", "tls_disable"}:
        return await registry.tls_control(command.removeprefix("tls_"))
    if command == "data_reset_cp":
        cp = request.get("charger")
        if not isinstance(cp, str) or not cp or "/" in cp:
            return {"error": "invalid_charger"}
        if cp in registry.connected_chargers():
            return {"error": "charger_connected", "charger": cp}
        try:
            return {"ok": True, "response": await registry.reset_cp_data(cp)}
        except (ValueError, OSError, RuntimeError) as exc:
            return {"error": "data_reset_failed", "detail": str(exc)}
    charger, request, resolution_error = _resolve_charger(registry, request)
    if resolution_error is not None:
        return resolution_error
    assert charger is not None
    session = registry.session(charger)
    if session is None:
        return {"error": "charger_not_connected", "charger": charger}

    try:
        if command in _DATA_COMMANDS:
            return await dispatch_data(registry, session, charger, command, request)
        if command in _OPERATIONS_COMMANDS:
            return await dispatch_operations(registry, session, charger, command, request)
        if command in _RFID_COMMANDS:
            return await dispatch_rfid(registry, session, charger, command, request)
        if command in _SMART_COMMANDS:
            return await dispatch_smart(registry, session, charger, command, request)
        return {"error": "unknown_command", "command": command}
    except Exception as exc:
        return {"error": "command_failed", "detail": str(exc)}
