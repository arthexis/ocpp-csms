"""Strict, backward-compatible operator overrides for routed TCP/80 Discover.

A missing policy permits the configured service interface by default; a present
explicit disable wins. File ownership and no-follow checks remain unchanged.
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
    """Malformed or untrusted operator policy."""


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
    # Legacy attestation is parsed for compatibility, not required as proof.
    return RoutedHttpPolicy(enabled=payload["enabled"], interface=interface)


def load_policy(
    path: str | Path = DEFAULT_POLICY_PATH,
    *,
    owner_uid: int = 0,
    default_interface: str | None = None,
) -> RoutedHttpPolicy:
    """Read existing root-owned, private, regular policy; never create state.

    A missing file enables only ``default_interface`` when supplied by the
    service; direct callers without it retain the historical disabled default.
    Existing trusted explicit disables remain authoritative.
    """
    if default_interface is not None and (
        not isinstance(default_interface, str) or not _INTERFACE.fullmatch(default_interface)
        or default_interface == "lo"
    ):
        raise RoutedHttpPolicyError("invalid_routed_http_interface")
    implicit = RoutedHttpPolicy(enabled=default_interface is not None, interface=default_interface)
    file = Path(path)
    if not file.is_absolute():
        raise RoutedHttpPolicyError("routed_http_policy_path_not_absolute")

    # Follow the entire path through pinned descriptors, refusing symlinks at
    # every level. A trusted sticky root-owned /tmp is permitted for hermetic
    # tests, but every non-sticky writable ancestor is refused.
    directory_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_DIRECTORY
    try:
        descriptor = os.open("/", directory_flags)
    except OSError as exc:
        raise RoutedHttpPolicyError("untrusted_routed_http_policy_directory") from exc
    try:
        parts = file.parts[1:]
        for position, component in enumerate(parts[:-1]):
            try:
                child = os.open(component, directory_flags, dir_fd=descriptor)
            except FileNotFoundError as exc:
                # An uninstalled Discover has no policy directory. Preserve
                # default-off only for that exact absent final parent.
                if file == DEFAULT_POLICY_PATH and position == len(parts) - 2:
                    return implicit
                raise RoutedHttpPolicyError("untrusted_routed_http_policy_directory") from exc
            except OSError as exc:
                raise RoutedHttpPolicyError("untrusted_routed_http_policy_directory") from exc
            os.close(descriptor)
            descriptor = child
            directory_info = os.fstat(descriptor)
            final_parent = position == len(parts) - 2
            owner_valid = (
                directory_info.st_uid == owner_uid if final_parent
                else directory_info.st_uid in (0, owner_uid)
            )
            writable = bool(directory_info.st_mode & (stat.S_IWGRP | stat.S_IWOTH))
            trusted_sticky = (
                not final_parent
                and directory_info.st_uid == 0
                and bool(directory_info.st_mode & stat.S_ISVTX)
            )
            if (not stat.S_ISDIR(directory_info.st_mode) or not owner_valid
                    or (writable and not trusted_sticky)):
                raise RoutedHttpPolicyError("untrusted_routed_http_policy_directory")

        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
        try:
            policy_fd = os.open(parts[-1], flags, dir_fd=descriptor)
        except FileNotFoundError:
            return implicit
        except OSError as exc:
            raise RoutedHttpPolicyError("untrusted_routed_http_policy_file") from exc
    finally:
        os.close(descriptor)

    try:
        with os.fdopen(policy_fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != owner_uid:
                raise RoutedHttpPolicyError("untrusted_routed_http_policy_file")
            if stat.S_IMODE(info.st_mode) != 0o600:
                raise RoutedHttpPolicyError("insecure_routed_http_policy_permissions")
            if info.st_size > 4096:
                raise RoutedHttpPolicyError("oversized_routed_http_policy")
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise RoutedHttpPolicyError("oversized_routed_http_policy")

        def strict_pairs(pairs):
            output = {}
            for key, value in pairs:
                if key in output:
                    raise RoutedHttpPolicyError("duplicate_routed_http_policy_key")
                output[key] = value
            return output

        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=strict_pairs)
    except (UnicodeError, OSError, json.JSONDecodeError) as exc:
        raise RoutedHttpPolicyError("unreadable_routed_http_policy") from exc
    return parse_policy(payload)
