"""Opt-in isolated synthetic Discover lab for issue #355.

No import-time side effects. The outer launcher can only start a bubblewrap
sandbox with a fresh network namespace, read-only host root, and tmpfs state.
The inner probe refuses execution without independently checked isolation.
This is a wire-evidence/SQLite/nft smoke, NOT field or end-to-end EVSE proof.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile

from ocpp_csms.schema import DATABASE_FILENAME
from ocpp_discover import first_contact, provisional
from ocpp_discover.websocket import _websocket_receipt

_PARENT_NETNS = "OCPP_DISCOVER_LAB_PARENT_NETNS"
_TMPFS_PATHS = ("/tmp", "/run", "/var/lib", "/etc")
_CHARGER = "EVREADY_G3_SYNTHETIC"
_SOURCE = "198.18.0.12"
_DESTINATION = "198.18.0.1"
_PORT = 49152


def sandbox_command(python_executable: str, parent_netns: str) -> list[str]:
    """Build immutable, read-only-host isolation; no shell, sudo or host nft.

    bwrap --unshare-all also creates a new user/net/mount/IPC/PID namespace.
    The private tmpfs /var/lib masks field Discover state and /etc masks
    machine policy/credentials. Production filesystem remains read-only.
    """
    if not Path(python_executable).is_absolute():
        raise ValueError("lab_python_must_be_absolute")
    if not parent_netns.startswith("net:[") or not parent_netns.endswith("]"):
        raise ValueError("invalid_parent_network_namespace")
    command = ["bwrap", "--die-with-parent", "--new-session", "--unshare-all",
               "--ro-bind", "/", "/"]
    for directory in _TMPFS_PATHS:
        command += ["--tmpfs", directory]
    command += ["--proc", "/proc", "--dev", "/dev", "--chdir", "/tmp",
                "--setenv", _PARENT_NETNS, parent_netns,
                "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
                "--", python_executable, "-m", "ocpp_discover.lab_sandbox", "--inside"]
    return command


def require_sandbox() -> None:
    """A forged environment variable cannot by itself authorize a lab."""
    parent = os.environ.get(_PARENT_NETNS, "")
    current = os.readlink("/proc/self/ns/net")
    if not parent.startswith("net:[") or current == parent:
        raise RuntimeError("lab_network_namespace_not_isolated")
    if not all(os.path.ismount(path) for path in _TMPFS_PATHS):
        raise RuntimeError("lab_writable_state_not_tmpfs")
    # Require a read-only root, not a caller-controlled label/flag.
    root = next((line for line in Path("/proc/self/mountinfo").read_text().splitlines()
                 if line.split()[4] == "/"), "")
    if not root or "ro" not in root.split(" - ", 1)[0].split()[5].split(","):
        raise RuntimeError("lab_host_root_not_read_only")
    if Path("/var/lib/ocpp-discover/discovered.json").exists():
        raise RuntimeError("lab_field_state_visible")


def _nft(arguments: list[str], *, text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["nft", *arguments], input=text, text=True,
                          capture_output=True, check=True, timeout=10)


def isolated_probe() -> dict[str, object]:
    """Real nft operations are allowed exclusively in the isolated netns.

    This verifies a realistic EV Ready-shaped SYN/WebSocket/BootNotification
    *evidence* trace against disposable SQLite and transient nftables. It does
    not connect to an actual CSMS or charger and must not promote field state.
    """
    require_sandbox()
    if shutil.which("nft") is None:
        raise RuntimeError("lab_nft_missing")
    with tempfile.TemporaryDirectory(prefix="ocpp-discover-355-", dir="/tmp") as tmp:
        root = Path(tmp)
        data_dir = root / "schemas" / "8"
        data_dir.mkdir(parents=True)
        database = data_dir / DATABASE_FILENAME
        with sqlite3.connect(database) as db:
            db.executescript("""
                CREATE TABLE discovery_sessions (
                    session_id TEXT PRIMARY KEY, charger_id TEXT,
                    remote_ip TEXT, remote_port INTEGER
                );
                CREATE TABLE events (
                    id INTEGER PRIMARY KEY, direction TEXT, charger_id TEXT
                );
                CREATE TABLE discovery_event_sessions (
                    event_id INTEGER, session_id TEXT
                );
            """)
            db.execute("INSERT INTO events VALUES (1, 'in', ?)", (_CHARGER,))
            db.commit()

        watermark = first_contact._event_watermark(data_dir)
        if watermark != 1:
            raise RuntimeError("lab_watermark_mismatch")

        capture = (
            f"12:00:00.000001 IP {_SOURCE}.{_PORT} > {_DESTINATION}.80: "
            "Flags [P.], seq 1, length 180\n"
            f"GET /ocpp/{_CHARGER} HTTP/1.1\r\n"
            f"Host: {_DESTINATION}\r\n"
            "Upgrade: websocket\r\nConnection: keep-alive, Upgrade\r\n\r\n"
        )
        receipt = _websocket_receipt(
            capture, interface="eth0", listen_port=9000,
            source_ip=_SOURCE, destination_filter={_DESTINATION},
        )
        ports = first_contact._captured_source_ports(capture, _SOURCE, _CHARGER)
        candidate = provisional.LocalSynCandidate(
            "eth0", _SOURCE, _DESTINATION, 80, 9000)
        if ports != {_PORT} or not provisional.matches(candidate, receipt):
            raise RuntimeError("lab_websocket_scope_mismatch")
        if first_contact._session_proven(data_dir, _CHARGER, _SOURCE, ports, watermark):
            raise RuntimeError("lab_unproven_session_accepted")

        with sqlite3.connect(database) as db:
            db.execute("INSERT INTO discovery_sessions VALUES (?, ?, ?, ?)",
                       ("session-1", _CHARGER, _SOURCE, _PORT))
            db.execute("INSERT INTO events VALUES (2, 'in', ?)", (_CHARGER,))
            db.execute("INSERT INTO discovery_event_sessions VALUES (2, 'session-1')")
            db.commit()
        if not first_contact._session_proven(data_dir, _CHARGER, _SOURCE, ports, watermark):
            raise RuntimeError("lab_fresh_session_not_proven")
        with sqlite3.connect(database) as db:
            db.execute("INSERT INTO discovery_sessions VALUES (?, ?, ?, ?)",
                       ("reused-peer", _CHARGER, _SOURCE, _PORT))
            db.commit()
        if first_contact._session_proven(data_dir, _CHARGER, _SOURCE, ports, watermark):
            raise RuntimeError("lab_ambiguous_peer_accepted")

        ruleset = provisional._ruleset(candidate)
        if ('iifname "eth0"' not in ruleset or "tcp dport 80" not in ruleset
                or "ip saddr " + _SOURCE not in ruleset
                or "ip daddr " + _DESTINATION not in ruleset):
            raise RuntimeError("lab_redirect_scope_broadened")
        _nft(["-c", "-f", "-"], text=ruleset)
        installed = False
        try:
            _nft(["-f", "-"], text=ruleset)
            installed = True
            _nft(["list", "table", "ip", "ocpp_field_redirect"])
        finally:
            if installed:
                _nft(["delete", "table", "ip", "ocpp_field_redirect"])
        if Path("/var/lib/ocpp-discover/discovered.json").exists():
            raise RuntimeError("lab_field_state_modified")
        return {"isolated": True, "temporary_nft_cleaned": True,
                "disposable_sqlite": True, "session_proof": "fresh_and_ambiguous_checked",
                "field_persistence": False, "network_packet_replay": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Isolated synthetic Discover proof, never field acceptance")
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.inside:
        print(json.dumps(isolated_probe(), sort_keys=True))
        return 0
    for binary in ("bwrap", "nft"):
        if shutil.which(binary) is None:
            raise RuntimeError("lab_dependency_missing_" + binary)
    parent = os.readlink("/proc/self/ns/net")
    command = sandbox_command(sys.executable, parent)
    return subprocess.run(command, check=False, timeout=120).returncode


if __name__ == "__main__":
    raise SystemExit(main())
