from __future__ import annotations

import asyncio
from typing import Any

from .protocols import Session, SessionRegistry

def _response_payload(response: Any) -> dict[str, Any]:
    payload = getattr(response, "__dict__", None)
    if isinstance(payload, dict):
        return dict(payload)
    return {"status": getattr(response, "status", None)}


def _configuration_keys(request: dict[str, Any]) -> list[str] | None | object:
    keys = request.get("keys")
    if keys is None or keys == []:
        return None
    if not isinstance(keys, list):
        return _INVALID
    if any(not isinstance(key, str) or not key for key in keys):
        return _INVALID
    return keys


def _non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _resolve_charger(registry: SessionRegistry, request: dict[str, Any]) -> tuple[str | None, dict[str, Any], dict[str, Any] | None]:
    normalized = dict(request)
    connected = registry.connected_chargers()
    charger = normalized.get("charger")
    if charger is not None:
        if not isinstance(charger, str) or not charger:
            return None, normalized, {"error": "invalid_charger"}
        return charger, normalized, None
    if normalized.get("command") == "config":
        keys = normalized.get("keys")
        if isinstance(keys, list) and keys and isinstance(keys[0], str) and keys[0] in connected:
            charger = keys[0]
            normalized["keys"] = keys[1:]
            return charger, normalized, None
    if len(connected) == 1:
        return connected[0], normalized, None
    if not connected:
        return None, normalized, {"error": "no_charger_connected"}
    return None, normalized, {"error": "charger_required", "chargers": connected}


def _config_guard(registry: SessionRegistry, charger: str, force: Any, *, change: bool) -> dict[str, Any] | None:
    if not isinstance(force, bool):
        return {"error": "invalid_force"}
    active_transactions = registry.active_transaction_ids(charger)
    if active_transactions and not force:
        registry.record_control_event(
            "configuration_change_blocked" if change else "configuration_query_blocked",
            charger_id=charger,
            details={"transactions": active_transactions},
        )
        return {"error": "active_transaction", "charger": charger, "transactions": active_transactions}
    if active_transactions and force:
        registry.record_control_event(
            "configuration_change_forced" if change else "configuration_query_forced",
            charger_id=charger,
            details={"transactions": active_transactions},
        )
    return None


async def _composite_schedule_response(
    registry: SessionRegistry,
    session: Session,
    charger: str,
    connector: int,
    duration: int,
    charging_rate_unit: str | None,
) -> dict[str, Any]:
    response = await session.get_composite_schedule(connector, duration, charging_rate_unit)
    payload = _response_payload(response)
    if connector != 0 or payload.get("status") != "Rejected":
        return payload

    physical_connectors = registry.physical_connector_ids(charger)
    if not physical_connectors:
        return payload

    schedules: list[dict[str, Any]] = []
    all_accepted = True
    for connector_id in physical_connectors:
        physical_response = await session.get_composite_schedule(
            connector_id,
            duration,
            charging_rate_unit,
        )
        physical_payload = _response_payload(physical_response)
        schedules.append({"connector_id": connector_id, "response": physical_payload})
        if physical_payload.get("status") != "Accepted":
            all_accepted = False

    return {
        "status": "Accepted" if all_accepted else "Rejected",
        "requested_connector": 0,
        "compatibility_fallback": "physical_connectors",
        "schedules": schedules,
    }


_INVALID = object()


def _rfid_entries(request: dict[str, Any]) -> list[dict[str, Any]] | object:
    entries = request.get("entries")
    if not isinstance(entries, list):
        return _INVALID
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            return _INVALID
        rfid = entry.get("rfid")
        name = entry.get("name")
        enabled = entry.get("enabled", True)
        if not isinstance(rfid, str) or not rfid or rfid in seen:
            return _INVALID
        if name is not None and not isinstance(name, str):
            return _INVALID
        if enabled is not True:
            return _INVALID
        seen.add(rfid)
        normalized.append({"rfid": rfid, "name": name, "enabled": True})
    return normalized


def _list_version(response: Any) -> int:
    payload = _response_payload(response)
    value = payload.get("list_version")
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("charger returned an invalid local-list version")
    return value


RFID_CONFIGURATION_KEYS = [
    "LocalAuthListEnabled",
    "AuthorizationCacheEnabled",
    "LocalAuthorizeOffline",
    "LocalPreAuthorize",
    "AllowOfflineTxForUnknownId",
    "StopTransactionOnInvalidId",
    "MaxEnergyOnInvalidId",
    "AuthorizeRemoteTxRequests",
    "SupportedFeatureProfiles",
    "LocalAuthListMaxLength",
    "SendLocalListMaxLength",
]


async def _rfid_configuration(session: Session) -> dict[str, Any]:
    try:
        return _response_payload(await session.get_configuration(RFID_CONFIGURATION_KEYS))
    except Exception as exc:
        return {"error": str(exc)}


async def _send_rfid_list(
    registry: SessionRegistry,
    session: Session,
    charger: str,
    *,
    entries: list[dict[str, Any]],
    source_file: str | None,
    list_hash: str,
    clear: bool,
) -> dict[str, Any]:
    configuration = await _rfid_configuration(session)
    current_response = await session.get_local_list_version()
    current_version = _list_version(current_response)
    recorded_version = registry.latest_rfid_list_version(charger)
    if clear:
        list_version = 0
    else:
        candidates = [0, current_version]
        if recorded_version is not None:
            candidates.append(recorded_version)
        list_version = max(candidates) + 1

    sent = await session.send_local_list(list_version, entries)
    sent_payload = _response_payload(sent)
    status = sent_payload.get("status")
    result: dict[str, Any] = {
        "status": status,
        "charger": charger,
        "previous_version": current_version,
        "list_version": list_version,
        "cards": len(entries),
        "configuration": configuration,
    }
    if status != "Accepted":
        return result

    verified_version: int | None = None
    verification_error: str | None = None
    try:
        verified_version = _list_version(await session.get_local_list_version())
    except Exception as exc:
        verification_error = str(exc)

    try:
        history_id = registry.record_rfid_list(
            charger,
            list_version=list_version,
            entries=entries,
            source_file=source_file,
            list_hash=list_hash,
            verified_version=verified_version,
        )
    except Exception as exc:
        return {
            "error": "accepted_not_recorded",
            "detail": str(exc),
            **result,
            "verified_version": verified_version,
        }

    result["history_id"] = history_id
    result["verified_version"] = verified_version
    if verification_error is not None:
        result["verification_error"] = verification_error
    return result


def _control_timing(request: dict[str, Any]) -> tuple[str | None, int | None, dict[str, Any] | None]:
    timing = request.get("timing")
    seconds = request.get("seconds")
    if timing not in {"now", "after", "within"}:
        return None, None, {"error": "invalid_timing"}
    if timing == "now":
        if seconds is not None:
            return None, None, {"error": "invalid_timing"}
        return timing, None, None
    if not _positive_int(seconds):
        return None, None, {"error": "invalid_timing_seconds"}
    return timing, seconds, None


def _blocking_transactions(
    registry: SessionRegistry,
    charger: str,
    *,
    command: str,
    connector: int | None,
) -> list[int]:
    if command == "reset":
        return registry.active_transaction_ids(charger)
    if command != "start":
        return []
    active = registry.active_transactions(charger)
    if connector is None:
        return [transaction_id for transaction_id, _connector_id in active]
    return [
        transaction_id
        for transaction_id, connector_id in active
        if connector_id == connector
    ]


async def _wait_for_control_window(
    registry: SessionRegistry,
    charger: str,
    *,
    command: str,
    connector: int | None,
    timing: str,
    seconds: int | None,
) -> dict[str, Any] | None:
    if timing == "after":
        assert seconds is not None
        await asyncio.sleep(seconds)

    active = _blocking_transactions(registry, charger, command=command, connector=connector)
    if not active:
        return None

    if timing != "within":
        return {"error": "active_transaction", "charger": charger, "transactions": active}

    assert seconds is not None
    for _ in range(seconds):
        await asyncio.sleep(1)
        active = _blocking_transactions(registry, charger, command=command, connector=connector)
        if not active:
            return None
    return {
        "error": "active_transaction_timeout",
        "charger": charger,
        "transactions": active,
        "seconds": seconds,
    }


async def dispatch_control(registry: SessionRegistry, request: dict[str, Any]) -> dict[str, Any]:
    command = request.get("command")
    if not isinstance(command, str) or not command:
        return {"error": "missing_command"}
    if command in {"tls_status", "tls_enable", "tls_reload", "tls_disable"}:
        return await registry.tls_control(command.removeprefix("tls_"))
    charger, request, resolution_error = _resolve_charger(registry, request)
    if resolution_error is not None:
        return resolution_error
    assert charger is not None
    session = registry.session(charger)
    if session is None:
        return {"error": "charger_not_connected", "charger": charger}

    timing, seconds, timing_error = _control_timing(request)
    if command in {"start", "stop", "reset"}:
        if timing_error is not None:
            return timing_error
        assert timing is not None
        guard = await _wait_for_control_window(
            registry,
            charger,
            command=command,
            connector=request.get("connector") if command == "start" else None,
            timing=timing,
            seconds=seconds,
        )
        if guard is not None:
            return guard
        session = registry.session(charger)
        if session is None:
            return {"error": "charger_not_connected", "charger": charger}

    try:
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
            guard = _config_guard(registry, charger, request.get("force", False), change=False)
            if guard is not None:
                return guard
            response = await session.get_configuration(keys)
        elif command == "config_set":
            key = request.get("key")
            value = request.get("value")
            if not isinstance(key, str) or not key:
                return {"error": "invalid_key"}
            if not isinstance(value, str):
                return {"error": "invalid_value"}
            guard = _config_guard(registry, charger, request.get("force", False), change=True)
            if guard is not None:
                return guard
            changed = await session.change_configuration(key, value)
            readback = await session.get_configuration([key])
            return {
                "ok": True,
                "response": {
                    "change": _response_payload(changed),
                    "readback": _response_payload(readback),
                },
            }
        elif command == "rfid_version":
            response = await session.get_local_list_version()
            payload = _response_payload(response)
            payload["charger"] = charger
            payload["configuration"] = await _rfid_configuration(session)
            return {"ok": True, "response": payload}
        elif command == "rfid_export":
            entries = _rfid_entries(request)
            if entries is _INVALID:
                return {"error": "invalid_rfid_entries"}
            source_file = request.get("source_file")
            list_hash = request.get("list_hash")
            if source_file is not None and (not isinstance(source_file, str) or not source_file):
                return {"error": "invalid_source_file"}
            if not isinstance(list_hash, str) or not list_hash:
                return {"error": "invalid_list_hash"}
            result = await _send_rfid_list(
                registry,
                session,
                charger,
                entries=entries,
                source_file=source_file,
                list_hash=list_hash,
                clear=False,
            )
            return {"ok": True, "response": result} if "error" not in result else result
        elif command == "rfid_clear":
            result = await _send_rfid_list(
                registry,
                session,
                charger,
                entries=[],
                source_file=None,
                list_hash="empty",
                clear=True,
            )
            return {"ok": True, "response": result} if "error" not in result else result
        elif command == "set_charging_profile":
            connector = request.get("connector")
            profile = request.get("profile")
            if not _non_negative_int(connector):
                return {"error": "invalid_connector"}
            if not isinstance(profile, dict):
                return {"error": "invalid_profile"}
            response = await session.set_charging_profile(connector, profile)
        elif command == "clear_charging_profile":
            profile_id = request.get("id")
            connector = request.get("connector")
            purpose = request.get("purpose")
            stack_level = request.get("stack_level")
            if profile_id is not None and not _non_negative_int(profile_id):
                return {"error": "invalid_profile_id"}
            if connector is not None and not _non_negative_int(connector):
                return {"error": "invalid_connector"}
            if purpose is not None and (not isinstance(purpose, str) or not purpose):
                return {"error": "invalid_purpose"}
            if stack_level is not None and not _non_negative_int(stack_level):
                return {"error": "invalid_stack_level"}
            response = await session.clear_charging_profile(
                profile_id=profile_id,
                connector_id=connector,
                purpose=purpose,
                stack_level=stack_level,
            )
        elif command == "get_composite_schedule":
            connector = request.get("connector")
            duration = request.get("duration")
            charging_rate_unit = request.get("charging_rate_unit")
            if not _non_negative_int(connector):
                return {"error": "invalid_connector"}
            if not _positive_int(duration):
                return {"error": "invalid_duration"}
            if charging_rate_unit is not None and charging_rate_unit not in {"A", "W"}:
                return {"error": "invalid_charging_rate_unit"}
            return {
                "ok": True,
                "response": await _composite_schedule_response(
                    registry,
                    session,
                    charger,
                    connector,
                    duration,
                    charging_rate_unit,
                ),
            }
        else:
            return {"error": "unknown_command", "command": command}
    except Exception as exc:
        return {"error": "command_failed", "detail": str(exc)}

    return {"ok": True, "response": _response_payload(response)}


