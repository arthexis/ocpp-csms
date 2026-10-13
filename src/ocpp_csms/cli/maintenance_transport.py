"""Shared control transport for maintenance commands; no OCPP policy decisions."""
from __future__ import annotations

import asyncio

from ocpp_csms.control import send_control


class MaintenanceControlError(Exception):
    """A request failed or its OCPP response was malformed."""


def maintenance_control(data_dir, request, *, malformed="invalid OCPP response"):
    """Return the OCPP response mapping, preserving command-specific status semantics.

    A transport acknowledgment is not proof that a physical action completed.
    """
    try:
        envelope = asyncio.run(send_control(data_dir, request))
    except (ConnectionError, FileNotFoundError, OSError, ValueError) as exc:
        raise MaintenanceControlError(str(exc)) from exc
    if not isinstance(envelope, dict):
        raise MaintenanceControlError(malformed)
    if envelope.get("error"):
        detail = envelope.get("detail") or envelope.get("charger")
        suffix = f": {detail}" if detail else ""
        raise MaintenanceControlError(f"{envelope['error']}{suffix}")
    payload = envelope.get("response")
    if not isinstance(payload, dict):
        raise MaintenanceControlError(malformed)
    return payload
