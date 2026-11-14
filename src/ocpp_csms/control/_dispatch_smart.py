"""smart control command family; preserves original request validation order."""
from __future__ import annotations

from typing import Any
from .protocols import Session, SessionRegistry
from ._dispatch_support import _response_payload, _smart_charging_unsupported, _composite_schedule_response
from ._dispatch_validation import _non_negative_int, _positive_int

async def dispatch_smart(registry: SessionRegistry, session: Session, charger: str, command: str, request: dict[str, Any]) -> dict[str, Any]:
    if command == "set_charging_profile":
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
        force = request.get("force", False)
        if not isinstance(force, bool):
            return {"error": "invalid_force"}
        if not force and await _smart_charging_unsupported(session):
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
        force = request.get("force", False)
        if not isinstance(force, bool):
            return {"error": "invalid_force"}
        if not force and await _smart_charging_unsupported(session):
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
    return {"ok": True, "response": _response_payload(response)}
