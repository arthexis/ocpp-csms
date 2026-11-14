"""operations control command family; preserves original request validation order."""
from __future__ import annotations

from typing import Any
from .protocols import Session, SessionRegistry
from ._dispatch_support import _response_payload
from ._dispatch_validation import _configuration_keys, _INVALID, _non_negative_int, _positive_int

async def dispatch_operations(registry: SessionRegistry, session: Session, charger: str, command: str, request: dict[str, Any]) -> dict[str, Any]:
    if command == "rfid_cache_clear":
        response = await session.clear_cache()
    elif command == "trigger":
        message = request.get("message")
        connector = request.get("connector")
        if message not in {"BootNotification", "Heartbeat", "StatusNotification", "MeterValues", "DiagnosticsStatusNotification", "FirmwareStatusNotification"}:
            return {"error": "invalid_trigger_message"}
        if connector is not None and not _positive_int(connector):
            return {"error": "invalid_connector"}
        if connector is not None and message not in {"StatusNotification", "MeterValues"}:
            return {"error": "connector_not_applicable"}
        response = await session.trigger_message(message, connector)
    elif command == "availability":
        connector = request.get("connector", 0)
        availability_type = request.get("type")
        if not _non_negative_int(connector):
            return {"error": "invalid_connector"}
        if availability_type not in {"Operative", "Inoperative"}:
            return {"error": "invalid_availability_type"}
        response = await session.change_availability(connector, availability_type)
    elif command == "unlock":
        connector = request.get("connector")
        if not _positive_int(connector):
            return {"error": "invalid_connector"}
        response = await session.unlock_connector(connector)
    elif command == "start":
        id_tag = request.get("id_tag")
        connector = request.get("connector")
        if not isinstance(id_tag, str) or not id_tag:
            return {"error": "missing_id_tag"}
        if connector is not None and not _non_negative_int(connector):
            return {"error": "invalid_connector"}
        response = await session.remote_start(id_tag=id_tag, connector_id=connector)
    elif command == "stop":
        transaction = request.get("transaction")
        if not _non_negative_int(transaction):
            return {"error": "invalid_transaction"}
        response = await session.remote_stop(transaction)
    elif command == "reset":
        reset_type = request.get("type", "Soft")
        if reset_type not in {"Soft", "Hard"}:
            return {"error": "invalid_reset_type"}
        response = await session.reset(reset_type)
    elif command == "config":
        keys = _configuration_keys(request)
        if keys is _INVALID:
            return {"error": "invalid_keys"}
        response = await session.get_configuration(keys)
    elif command == "config_set":
        key = request.get("key")
        value = request.get("value")
        if not isinstance(key, str) or not key:
            return {"error": "invalid_key"}
        if not isinstance(value, str):
            return {"error": "invalid_value"}
        changed = await session.change_configuration(key, value)
        readback = await session.get_configuration([key])
        return {
            "ok": True,
            "response": {
                "change": _response_payload(changed),
                "readback": _response_payload(readback),
            },
        }
    return {"ok": True, "response": _response_payload(response)}
