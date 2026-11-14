"""Pure validators for control requests (behavior-preserving extraction)."""
from __future__ import annotations

from typing import Any

_INVALID = object()

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
