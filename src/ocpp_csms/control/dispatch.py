from __future__ import annotations

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
    if len(connected) == 1:
        return connected[0], normalized, None
    if not connected:
        return None, normalized, {"error": "no_charger_connected"}
    return None, normalized, {"error": "charger_required", "chargers": connected}


async def _smart_charging_unsupported(session: Session) -> bool:
    """Block only explicit lack of support; unknown capability remains unchanged."""
    try:
        response = await session.get_configuration(["SupportedFeatureProfiles"])
    except Exception:
        return False
    payload = _response_payload(response)
    for entry in payload.get("configuration_key") or []:
        if not isinstance(entry, dict) or entry.get("key") != "SupportedFeatureProfiles":
            continue
        value = entry.get("value")
        if isinstance(value, str):
            return "SmartCharging" not in {part.strip() for part in value.split(",")}
    return False


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
        if command == "data_transfer":
            vendor_id = request.get("vendor_id")
            message_id = request.get("message_id")
            data = request.get("data")
            if not isinstance(vendor_id, str) or not 1 <= len(vendor_id) <= 255:
                return {"error": "invalid_vendor_id"}
            if message_id is not None and (not isinstance(message_id, str) or not 1 <= len(message_id) <= 50):
                return {"error": "invalid_message_id"}
            if data is not None and not isinstance(data, str):
                return {"error": "invalid_data"}
            response = await session.data_transfer(vendor_id, message_id, data)
        elif command == "reserve":
            from datetime import datetime, timezone
            connector = request.get("connector")
            reservation_id = request.get("reservation_id")
            id_tag = request.get("id_tag")
            parent = request.get("parent_id_tag")
            expiry = request.get("expiry_date")
            if not _non_negative_int(connector):
                return {"error": "invalid_connector"}
            if not _positive_int(reservation_id):
                return {"error": "invalid_reservation_id"}
            if not isinstance(id_tag, str) or not 1 <= len(id_tag) <= 20:
                return {"error": "invalid_id_tag"}
            if parent is not None and (not isinstance(parent, str) or not 1 <= len(parent) <= 20):
                return {"error": "invalid_parent_id_tag"}
            if not isinstance(expiry, str):
                return {"error": "invalid_expiry"}
            try:
                when = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
                if when.tzinfo is None or when <= datetime.now(timezone.utc):
                    return {"error": "invalid_expiry"}
            except ValueError:
                return {"error": "invalid_expiry"}
            response = await session.reserve_now(connector, expiry, id_tag, reservation_id, parent)
        elif command == "cancel_reservation":
            reservation_id = request.get("reservation_id")
            if not _positive_int(reservation_id):
                return {"error": "invalid_reservation_id"}
            response = await session.cancel_reservation(reservation_id)
        elif command == "update_firmware":
            from datetime import datetime, timezone
            from urllib.parse import urlsplit
            location = request.get("location")
            retrieve_date = request.get("retrieve_date")
            if not isinstance(location, str) or not location:
                return {"error": "invalid_location"}
            try:
                parsed = urlsplit(location)
                if parsed.scheme.lower() not in {"http", "https", "ftp", "ftps"} or not parsed.hostname or parsed.fragment:
                    return {"error": "invalid_location"}
            except ValueError:
                return {"error": "invalid_location"}
            if not isinstance(retrieve_date, str):
                return {"error": "invalid_retrieve_date"}
            try:
                when = datetime.fromisoformat(retrieve_date.replace("Z", "+00:00"))
                if when.tzinfo is None:
                    return {"error": "timezone_required"}
            except ValueError:
                return {"error": "invalid_retrieve_date"}
            retries = request.get("retries")
            retry_interval = request.get("retry_interval")
            if retries is not None and not _non_negative_int(retries):
                return {"error": "invalid_retries"}
            if retry_interval is not None and not _non_negative_int(retry_interval):
                return {"error": "invalid_retry_interval"}
            response = await session.update_firmware(
                location, retrieve_date, retries=retries, retry_interval=retry_interval
            )
        elif command == "get_diagnostics":
            from urllib.parse import urlsplit
            from datetime import datetime
            location = request.get("location")
            if not isinstance(location, str) or not location:
                return {"error": "invalid_location"}
            try:
                parsed = urlsplit(location)
                if parsed.scheme.lower() not in {"http", "https", "ftp", "ftps"} or not parsed.hostname or parsed.fragment:
                    return {"error": "invalid_location"}
            except ValueError:
                return {"error": "invalid_location"}
            retries = request.get("retries")
            interval = request.get("retry_interval")
            if retries is not None and not _non_negative_int(retries):
                return {"error": "invalid_retries"}
            if interval is not None and not _non_negative_int(interval):
                return {"error": "invalid_retry_interval"}
            start_time, stop_time = request.get("start_time"), request.get("stop_time")
            try:
                start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00")) if start_time else None
                stop_dt = datetime.fromisoformat(stop_time.replace("Z", "+00:00")) if stop_time else None
                if any(dt.tzinfo is None for dt in (start_dt, stop_dt) if dt is not None):
                    return {"error": "timezone_required"}
                if start_dt and stop_dt and start_dt > stop_dt:
                    return {"error": "invalid_time_window"}
            except (TypeError, ValueError, AttributeError):
                return {"error": "invalid_time_window"}
            response_timeout = request.get("response_timeout", 30)
            if response_timeout is not None and (type(response_timeout) is not int or response_timeout < 1):
                return {"error": "invalid_response_timeout"}
            response = await session.get_diagnostics(
                location, retries=retries, retry_interval=interval,
                start_time=start_time, stop_time=stop_time,
                response_timeout=response_timeout,
            )
        elif command == "rfid_cache_clear":
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
            force = request.get("force", False)
            if not isinstance(force, bool):
                return {"error": "invalid_force"}
            if not force and await _smart_charging_unsupported(session):
                return {"error": "smart_charging_not_advertised", "detail": "Charger does not advertise SmartCharging in SupportedFeatureProfiles"}
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
            if await _smart_charging_unsupported(session):
                return {"error": "smart_charging_not_advertised", "detail": "Charger does not advertise SmartCharging in SupportedFeatureProfiles"}
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
            if await _smart_charging_unsupported(session):
                return {"error": "smart_charging_not_advertised", "detail": "Charger does not advertise SmartCharging in SupportedFeatureProfiles"}
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


