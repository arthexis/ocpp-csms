"""Pure OCPP observed-state calculations; historical open transactions do not imply charging."""
from __future__ import annotations

from typing import Any


def derive_status(status: str | None, error_code: str | None, info: str | None) -> str | None:
    return 'EmergencyStop' if (status == 'Faulted' and error_code == 'InternalError' and info == 'EmergencyStop') else status


def operational_state(connectors: list[Any]) -> tuple[str, str]:
    """Customer availability and observed activity are independent."""
    physical = [item for item in connectors if item.connector_id > 0]
    availability = (
        "available" if any(item.status == "Available" for item in physical)
        else "unavailable" if physical and all(item.status in {
            "Charging", "SuspendedEV", "SuspendedEVSE", "Preparing",
            "Finishing", "Faulted", "Unavailable", "Reserved",
        } for item in physical)
        else "unknown"
    )
    activity = (
        "active" if any(item.status in {"Charging", "SuspendedEV", "SuspendedEVSE"} for item in physical)
        else "inactive" if physical and all(item.status in {"Available", "Unavailable", "Faulted"} for item in physical)
        else "unknown"
    )
    return availability, activity


