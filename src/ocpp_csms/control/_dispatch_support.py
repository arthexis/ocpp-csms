"""Helpers shared by control command families (no I/O at import time)."""
from __future__ import annotations

from typing import Any
from .protocols import Session, SessionRegistry

def _response_payload(response: Any) -> dict[str, Any]:
    payload = getattr(response, "__dict__", None)
    if isinstance(payload, dict):
        return dict(payload)
    return {"status": getattr(response, "status", None)}

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
