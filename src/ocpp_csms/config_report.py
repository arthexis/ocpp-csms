from __future__ import annotations

from typing import Any


REDACTED = "[REDACTED]"
_SENSITIVE_EXACT_KEYS = {
    "rfidtagfreecharging",
    "tykey",
}
_SENSITIVE_KEY_FRAGMENTS = (
    "password",
    "passwd",
    "pwd",
    "passphrase",
    "secret",
    "token",
    "psk",
    "authorizationkey",
    "authkey",
    "privatekey",
    "apikey",
    "accesskey",
    "clientsecret",
)


def _normalized_key_name(key: str) -> str:
    return "".join(character for character in key.lower() if character.isalnum())


def sensitive_configuration_key(key: str) -> bool:
    normalized = _normalized_key_name(key)
    return normalized in _SENSITIVE_EXACT_KEYS or any(
        fragment in normalized for fragment in _SENSITIVE_KEY_FRAGMENTS
    )


def configuration_snapshot(
    payload: dict[str, Any],
    *,
    charger: str | None,
    show_sensitive: bool = False,
) -> dict[str, Any]:
    rows = payload.get("configuration_key", []) or []
    unknown = payload.get("unknown_key", []) or []
    if not isinstance(rows, list) or not isinstance(unknown, list):
        raise ValueError("invalid configuration response")

    configuration: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid configuration response")
        key = row.get("key")
        readonly = row.get("readonly")
        value = row.get("value")
        if not isinstance(key, str) or not isinstance(readonly, bool):
            raise ValueError("invalid configuration response")
        if value is not None and not isinstance(value, str):
            raise ValueError("invalid configuration response")
        if not show_sensitive and sensitive_configuration_key(key):
            value = REDACTED
        configuration.append({"key": key, "readonly": readonly, "value": value})

    if any(not isinstance(key, str) for key in unknown):
        raise ValueError("invalid configuration response")

    return {
        "charger": charger,
        "configuration": configuration,
        "unknown": list(unknown),
    }


def format_configuration_snapshot(snapshot: dict[str, Any]) -> str:
    lines = [f"Charger: {snapshot.get('charger') or '-'}", "KEY\tACCESS\tVALUE"]
    for row in snapshot.get("configuration", []):
        if not isinstance(row, dict):
            raise ValueError("invalid configuration snapshot")
        lines.append(
            f"{row.get('key')}\t{'R' if row.get('readonly') else 'RW'}\t{row.get('value') or ''}"
        )
    for key in snapshot.get("unknown", []):
        lines.append(f"Unknown: {key}")
    return "\n".join(lines)
