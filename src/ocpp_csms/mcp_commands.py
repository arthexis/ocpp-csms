"""Typed MCP operations over the same Unix control client used by the CLI."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ocpp_csms.config_report import configuration_snapshot
from ocpp_csms.control import send_control

TIMEOUT = 6
MAX_INT = 2147483647

# Initial reviewed OCPP 1.6 keys, not arbitrary vendor keys or credentials.
KEYS = (
    "HeartbeatInterval", "MeterValueSampleInterval", "ClockAlignedDataInterval",
    "ConnectionTimeOut", "MinimumStatusDuration", "NumberOfConnectors",
    "SupportedFeatureProfiles", "GetConfigurationMaxKeys", "AuthorizeRemoteTxRequests",
    "LocalAuthListEnabled", "AuthorizationCacheEnabled", "LocalAuthorizeOffline",
    "LocalPreAuthorize", "AllowOfflineTxForUnknownId", "StopTransactionOnInvalidId",
    "StopTransactionOnEVSideDisconnect", "UnlockConnectorOnEVSideDisconnect",
    "TransactionMessageAttempts", "TransactionMessageRetryInterval",
)
TRIGGERS = ("BootNotification", "Heartbeat", "StatusNotification", "MeterValues",
            "DiagnosticsStatusNotification", "FirmwareStatusNotification")


class CommandError(ValueError):
    pass


def integer(minimum=0):
    return {"type": "integer", "minimum": minimum, "maximum": MAX_INT}


def choices(values):
    return {"type": "string", "enum": list(values)}


def string(maximum, minimum=1):
    return {"type": "string", "minLength": minimum, "maxLength": maximum}


CP = string(256)
SPECS = {
    "get_configuration": ("config", {"keys": {"type": "array", "items": choices(KEYS), "minItems": 1, "maxItems": 16, "uniqueItems": True}}, (), "Read selected reviewed configuration keys using live OCPP."),
    "change_configuration": ("config_set", {"key": choices(KEYS), "value": string(256, 0)}, (), "Change a reviewed configuration key; CSMS performs its existing readback."),
    "remote_start": ("start", {"connector_id": integer(1), "id_tag": string(20)}, (), "Request charging on an explicit physical connector."),
    "remote_stop": ("stop", {"transaction_id": integer(1)}, (), "Request stop of a transaction recorded for this charge point."),
    "reset": ("reset", {"reset_type": choices(("Soft", "Hard"))}, (), "Request an explicit charger reset."),
    "change_availability": ("availability", {"connector_id": integer(), "availability": choices(("Operative", "Inoperative"))}, (), "Change availability; connector zero targets the whole charge point."),
    "unlock_connector": ("unlock", {"connector_id": integer(1)}, (), "Request physical connector unlock."),
    "trigger_message": ("trigger", {"message": choices(TRIGGERS), "connector_id": integer(1)}, ("connector_id",), "Request a supported OCPP message; connector only for StatusNotification or MeterValues."),
    "clear_cache": ("rfid_cache_clear", {}, (), "Request clear of the charger authorization cache."),
}


def declarations():
    return [
        {"name": name, "description": description,
         "inputSchema": {"type": "object", "properties": {"charge_point_id": CP, **properties},
                         "required": ["charge_point_id", *[key for key in properties if key not in optional]],
                         "additionalProperties": False},
         "annotations": {"readOnlyHint": False, "destructiveHint": name != "get_configuration",
                         "idempotentHint": False, "openWorldHint": True}}
        for name, (_, properties, optional, description) in SPECS.items()
    ]


def _valid(value, schema):
    kind = schema["type"]
    if kind == "integer":
        return type(value) is int and schema["minimum"] <= value <= schema["maximum"]
    if kind == "string":
        if type(value) is not str:
            return False
        if "enum" in schema:
            return value in schema["enum"]
        return (schema["minLength"] <= len(value) <= schema["maxLength"]
                and not any(ord(ch) < 32 or ord(ch) == 127 for ch in value))
    if kind == "array":
        return (type(value) is list and schema["minItems"] <= len(value) <= schema["maxItems"]
                and all(_valid(item, schema["items"]) for item in value)
                and len(set(value)) == len(value))
    return False


def build_request(name, args):
    if name not in SPECS or type(args) is not dict:
        raise CommandError("invalid_arguments")
    command, properties, optional, _ = SPECS[name]
    schemas = {"charge_point_id": CP, **properties}
    required = set(schemas) - set(optional)
    if (not required <= set(args) <= set(schemas)
            or not all(_valid(value, schemas[key]) for key, value in args.items())
            or not args["charge_point_id"].strip()):
        raise CommandError("invalid_arguments")
    if name == "trigger_message" and "connector_id" in args and args["message"] not in {"StatusNotification", "MeterValues"}:
        raise CommandError("invalid_arguments")
    request = {"command": command, "charger": args["charge_point_id"]}
    aliases = {"connector_id": "connector", "transaction_id": "transaction",
               "reset_type": "type", "availability": "type"}
    request.update({aliases.get(key, key): value for key, value in args.items() if key != "charge_point_id"})
    return request


def _result(outcome, status=None, **extra):
    return {"outcome": outcome, "ocpp_status": status,
            "physical_outcome": "not_verified", "automatic_retry": False, **extra}


def _configuration(payload, args, keys):
    if not isinstance(payload, dict):
        raise ValueError("invalid configuration response")
    # Only return requested allowlisted keys. Never expose unexpected fields.
    rows = payload.get("configuration_key", []) or []
    unknown = payload.get("unknown_key", []) or []
    if not isinstance(rows, list) or not isinstance(unknown, list) or len(rows) > 64 or len(unknown) > 64:
        raise ValueError("invalid configuration response")
    selected = [row for row in rows if isinstance(row, dict) and row.get("key") in keys]
    if any(isinstance(row.get("value"), str) and len(row["value"]) > 256 for row in selected):
        raise ValueError("oversized configuration value")
    if not selected and not any(key in keys for key in unknown if isinstance(key, str)):
        raise ValueError("missing configuration response")
    return configuration_snapshot(
        {"configuration_key": selected, "unknown_key": [key for key in unknown if isinstance(key, str) and key in keys]},
        charger=args["charge_point_id"],
    )


def interpret(name, args, reply):
    if not isinstance(reply, dict):
        return _result("delivery_unknown")
    if reply.get("error") in ("charger_not_connected", "no_charger_connected", "charger_required"):
        return _result("not_dispatched", reason="charger_not_connected")
    if reply.get("error") or reply.get("ok") is not True or not isinstance(reply.get("response"), dict):
        return _result("delivery_unknown")
    payload = reply["response"]
    if name == "get_configuration":
        try:
            return _result("acknowledged", configuration=_configuration(payload, args, args["keys"]))
        except (ValueError, TypeError):
            return _result("delivery_unknown")
    if name == "change_configuration":
        change = payload.get("change")
        status = change.get("status") if isinstance(change, dict) else None
    else:
        status = payload.get("status")
    if type(status) is not str:
        return _result("delivery_unknown")
    accepted = {"Accepted"}
    rejected = {"Rejected"}
    if name == "unlock_connector":
        accepted, rejected = {"Unlocked"}, {"UnlockFailed", "NotSupported"}
    elif name == "trigger_message":
        rejected.add("NotImplemented")
    elif name == "change_configuration":
        rejected.add("NotSupported")
    if status in accepted:
        outcome = "acknowledged"
    elif status in rejected:
        outcome = "rejected"
    elif name == "change_availability" and status == "Scheduled":
        outcome = "scheduled"
    elif name == "change_configuration" and status == "RebootRequired":
        outcome = "reboot_required"
    else:
        return _result("delivery_unknown")
    result = _result(outcome, status)
    if name == "change_configuration":
        try:
            result["readback"] = _configuration(payload.get("readback"), args, [args["key"]])
        except (ValueError, TypeError):
            result["readback"] = None
    return result


async def send_once(data_dir: Path, request: dict[str, Any]):
    # Once invoked, a transport error cannot distinguish pre-send from post-send.
    # Never retry even if the caller's timeout expires.
    return await asyncio.wait_for(send_control(data_dir, request), timeout=TIMEOUT)


def execute(data_dir: Path, name: str, args: dict[str, Any], request: dict[str, Any]):
    try:
        reply = asyncio.run(send_once(data_dir, request))
    except (OSError, ValueError, RuntimeError, asyncio.TimeoutError):
        return _result("delivery_unknown")
    return interpret(name, args, reply)
