"""Network interface inspection helpers for discovery."""
from __future__ import annotations

import json
import shutil
import subprocess
from typing import Callable

from ocpp_discover.capture import _INTERFACE


def require_ip() -> None:
    if shutil.which("ip") is None:
        raise RuntimeError("ip_not_found")


def run_ip(command: list[str]) -> subprocess.CompletedProcess[str]:
    require_ip()
    return subprocess.run(command, text=True, capture_output=True, check=False)


def ip_error(result: subprocess.CompletedProcess[str], fallback: str) -> RuntimeError:
    detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else fallback
    return RuntimeError(detail)


def ipv4_addresses(payload: object) -> set[str]:
    try:
        return {
            str(info["local"])
            for item in payload
            for info in item.get("addr_info", [])
            if info.get("family") == "inet" and "local" in info
        }
    except (TypeError, KeyError):
        raise RuntimeError("invalid_ip_address_output") from None


def interface_addresses(interface: str, *, run: Callable = run_ip) -> set[str]:
    if not _INTERFACE.fullmatch(interface):
        raise ValueError("invalid_interface")
    result = run(["ip", "-j", "address", "show", "dev", interface])
    if result.returncode != 0:
        raise ip_error(result, "interface_address_query_failed")
    try:
        return ipv4_addresses(json.loads(result.stdout))
    except (TypeError, ValueError, KeyError):
        raise RuntimeError("invalid_ip_address_output") from None


def host_addresses(*, run: Callable = run_ip) -> set[str]:
    result = run(["ip", "-j", "address", "show"])
    if result.returncode != 0:
        raise ip_error(result, "host_address_query_failed")
    try:
        return ipv4_addresses(json.loads(result.stdout))
    except (TypeError, ValueError, KeyError):
        raise RuntimeError("invalid_ip_address_output") from None
