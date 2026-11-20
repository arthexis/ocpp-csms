"""Identify the charger-reachable local address for private OCPP diagnostics.

Use the *connected charger's configured OCPP server IP* rather than the
host's eth0 IP. Devices may have overlapping local networks and route to
the CSMS through a different (but still local) destination address.
Only a literal, host-owned, private IPv4 address is accepted.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import subprocess
from pathlib import Path


def service_data_dir(pid: int, proc_root: Path = Path("/proc")) -> str:
    if pid <= 0:
        raise ValueError("CSMS must be running")
    argv = (proc_root / str(pid) / "cmdline").read_bytes().split(b"\0")
    args = [v.decode("utf-8") for v in argv if v]
    for idx, arg in enumerate(args):
        if arg == "--data-dir" and idx + 1 < len(args):
            return args[idx + 1]
        if arg.startswith("--data-dir="):
            return arg.split("=", 1)[1]
    raise ValueError("CSMS service has no --data-dir argument")


def parse_domain_config(content: str) -> str:
    rows = [line.split("\t") for line in content.splitlines()]
    matching = [row[2].strip() for row in rows if len(row) >= 3 and row[0] == "sDomain"]
    if len(matching) != 1 or not matching[0]:
        raise ValueError("unique sDomain is missing from charger GetConfiguration")
    return matching[0]


def choose_server_ip(domain: str, local_ips: list[str]) -> str:
    try:
        target = ipaddress.IPv4Address(domain)
    except ipaddress.AddressValueError as exc:
        raise ValueError("charger sDomain must be a literal private IPv4 address") from exc
    if not target.is_private or target.is_loopback or target.is_link_local or target.is_multicast or target.is_unspecified:
        raise ValueError("charger sDomain is not a private unicast address")
    owned = {ipaddress.IPv4Address(address) for address in local_ips}
    if target not in owned:
        raise ValueError("charger sDomain is not assigned locally; refusing an unverified bind")
    return str(target)


def discover(cli: str, peer: str, proc_root: Path = Path("/proc")) -> dict[str, str]:
    peer_ip = ipaddress.IPv4Address(peer)
    if not peer_ip.is_private or peer_ip.is_loopback:
        raise ValueError("charger peer is not a private host")
    pid = int(subprocess.check_output(
        ["systemctl", "show", "ocpp-csms.service", "-p", "MainPID", "--value"],
        text=True, timeout=8).strip())
    data_dir = service_data_dir(pid, proc_root)
    status = json.loads(subprocess.check_output(
        [cli, "--data-dir", data_dir, "status", "-j"],
        text=True, timeout=12))
    online = [cp["id"] for cp in status["data"]["chargers"] if cp.get("connected")]
    if len(online) != 1:
        raise ValueError("expected exactly one connected CSMS charger")
    config = subprocess.check_output(
        [cli, "--data-dir", data_dir, "config", "--cp", online[0], "sDomain"],
        text=True, timeout=20)
    domain = parse_domain_config(config)
    ip_info = json.loads(subprocess.check_output(
        ["ip", "-4", "-j", "addr", "show"],
        text=True, timeout=8))
    local_ips = [addr["local"] for iface in ip_info for addr in iface.get("addr_info", [])
                 if addr.get("family") == "inet" and iface.get("ifname") in ("eth0", "wlan0")]
    server_ip = choose_server_ip(domain, local_ips)
    return {"server_ip": server_ip, "charge_point": online[0], "charger_ip": str(peer_ip)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", required=True, help="Installed ocpp-csms executable")
    parser.add_argument("--charger-ip", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(discover(args.cli, args.charger_ip), sort_keys=True))
    except (ValueError, KeyError, IndexError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        parser.error(f"cannot establish private diagnostic endpoint: {exc}")


if __name__ == "__main__":
    main()
