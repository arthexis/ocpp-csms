"""Read-only field readiness check before enabling private diagnostics.

It is normal for first boot to lack a charger. In that case the standard
installation still provisions the diagnostic receiver, without exposing
an unverified listening socket or accepting an arbitrary source IP.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import subprocess

from private_diagnostics_target import discover


def unique_eth0_peer() -> str:
    info = json.loads(subprocess.check_output(
        ["ip", "-4", "-j", "addr", "show", "dev", "eth0"], text=True, timeout=8))
    interfaces = [ipaddress.IPv4Interface(f"{a['local']}/{a['prefixlen']}")
                  for item in info for a in item.get("addr_info", [])
                  if a.get("family") == "inet"]
    if len(interfaces) != 1:
        raise ValueError("expected exactly one IPv4 address on eth0")
    neighbors = json.loads(subprocess.check_output(
        ["ip", "-j", "neigh", "show", "dev", "eth0"], text=True, timeout=8))
    found: set[str] = set()
    for row in neighbors:
        try:
            peer = ipaddress.IPv4Address(row["dst"])
        except (KeyError, ValueError):
            continue
        states = row.get("state", [])
        if isinstance(states, str):
            states = [states]
        if (row.get("lladdr") and not ({"FAILED", "INCOMPLETE"} & set(states))
                and peer != interfaces[0].ip and peer in interfaces[0].network):
            found.add(str(peer))
    if len(found) != 1:
        raise ValueError(f"expected exactly one charger on eth0; found {len(found)}")
    return next(iter(found))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", required=True)
    args = parser.parse_args()
    try:
        peer = unique_eth0_peer()
        print(json.dumps(discover(args.cli, peer), sort_keys=True))
    except (ValueError, KeyError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        parser.error(f"private diagnostics not ready: {exc}")


if __name__ == "__main__":
    main()
