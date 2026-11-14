"""Offline contract for operator-authorized routed HTTP discovery.

This module is intentionally NOT used by Discover's running service in B1.
It cannot activate redirects, change nftables, or persist charger endpoints.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat

DEFAULT_POLICY_PATH = Path("/etc/ocpp-discover/routed-http-policy.json")
_INTERFACE = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")
_KEYS = frozenset({"version", "enabled", "interface", "dedicated_charger_network_verified"})


class RoutedHttpPolicyError(ValueError):
    """Malformed, unauthorized or unsafe operator opt-in."""


@dataclass(frozen=True)
class RoutedHttpPolicy:
    enabled: bool
    interface: str | None = None

    def authorizes(self, interface: str) -> bool:
        return self.enabled and self.interface == interface


def parse_policy(payload: object) -> RoutedHttpPolicy:
    """Require a minimal, exact v1 declaration; no implicit boolean coercion."""
    if type(payload) is not dict or set(payload) != _KEYS:
        raise RoutedHttpPolicyError("invalid_routed_http_policy_keys")
    if type(payload["version"]) is not int or payload["version"] != 1:
        raise RoutedHttpPolicyError("invalid_routed_http_policy_version")
    if type(payload["enabled"]) is not bool:
        raise RoutedHttpPolicyError("invalid_routed_http_policy_enabled")
    if type(payload["dedicated_charger_network_verified"]) is not bool:
        raise RoutedHttpPolicyError("invalid_dedicated_network_attestation")
    interface = payload["interface"]
    if not isinstance(interface, str) or not _INTERFACE.fullmatch(interface) or interface == "lo":
        raise RoutedHttpPolicyError("invalid_routed_http_interface")
    if payload["enabled"] and not payload["dedicated_charger_network_verified"]:
        raise RoutedHttpPolicyError("dedicated_network_not_verified")
    return RoutedHttpPolicy(enabled=payload["enabled"], interface=interface)


def load_policy(
    path: str | Path = DEFAULT_POLICY_PATH,
    *,
    owner_uid: int = 0,
) -> RoutedHttpPolicy:
    """Read existing root-owned, private, regular policy; never create state.

    A missing file means disabled. An existing but malformed/untrusted policy
    raises, so callers must fail closed rather than enable interception.
    """
    file = Path(path)
    try:
        info = file.lstat()
    except FileNotFoundError:
        return RoutedHttpPolicy(enabled=False)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != owner_uid:
        raise RoutedHttpPolicyError("untrusted_routed_http_policy_file")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise RoutedHttpPolicyError("insecure_routed_http_policy_permissions")
    if info.st_size > 4096:
        raise RoutedHttpPolicyError("oversized_routed_http_policy")
    try:
        # Fail closed on duplicate keys, which JSON normally silently accepts.
        def strict_pairs(pairs):
            output = {}
            for key, value in pairs:
                if key in output:
                    raise RoutedHttpPolicyError("duplicate_routed_http_policy_key")
                output[key] = value
            return output

        with file.open("r", encoding="utf-8") as stream:
            payload = json.load(stream, object_pairs_hook=strict_pairs)
    except (UnicodeError, OSError, json.JSONDecodeError) as exc:
        raise RoutedHttpPolicyError("unreadable_routed_http_policy") from exc
    return parse_policy(payload)
