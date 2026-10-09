"""On-demand simulator check against the *installed* CSMS (never starts another server).

Requires the simulator already installed at ~/.local/bin/ocpp-simulator.
Uses a unique charge point identity and verifies new evidence in the shared database.
Only connects to the explicitly supplied local endpoint; does not modify service units.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import socket
import sqlite3
import subprocess
import time
import uuid


def event_rows(db: Path, cp: str, after: int) -> list[tuple[int, str]]:
    if not db.is_file():
        raise RuntimeError(f"CSMS database missing: {db}")
    with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=5) as connection:
        return connection.execute(
            "SELECT id, action FROM events WHERE charger_id = ? AND id > ? ORDER BY id",
            (cp, after),
        ).fetchall()


def last_id(db: Path) -> int:
    if not db.is_file():
        raise RuntimeError(f"CSMS database missing: {db}")
    with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=5) as connection:
        return int(connection.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0])


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify persistent CSMS with an on-demand simulated charge")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--data-dir", type=Path, default=Path.home() / "ocpp-csms-data")
    parser.add_argument("--simulator", type=Path, default=Path.home() / ".local/bin/ocpp-simulator")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if not args.simulator.is_file():
        parser.error(f"persistent simulator missing: {args.simulator}")
    db = args.data_dir.expanduser() / "ocpp-csms.sqlite3"
    cursor = last_id(db)
    cp = "SIM-INTEGRATION-" + uuid.uuid4().hex[:12].upper()
    # Restrict the test to the host's loopback interface. Never send test
    # transactions to an external or charger-owned endpoint.
    with socket.create_connection(("127.0.0.1", args.port), timeout=3):
        pass
    command = [
        str(args.simulator), "run", "--cp", cp,
        "--url", f"ws://127.0.0.1:{args.port}/{cp}",
        "--connector", "1", "--rfid", "SIM-INTEGRATION",
        "--power", "7.2", "--duration", "6", "--meter-interval", "1",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError(f"simulator failed ({result.returncode}): {result.stderr}")
    if "Transaction complete: 12 Wh" not in result.stdout:
        raise AssertionError(f"simulator did not report a complete charge: {result.stdout}")
    deadline = time.monotonic() + 15
    while True:
        rows = event_rows(db, cp, cursor)
        actions = {action for _, action in rows}
        if {"StartTransaction", "StopTransaction"} <= actions:
            break
        if time.monotonic() >= deadline:
            raise AssertionError(f"missing persisted transaction events for {cp}: {sorted(actions)}")
        time.sleep(0.25)
    print(f"PASS {cp}: simulator completed 12 Wh; CSMS persisted StartTransaction and StopTransaction")
    print(f"New event ids: {[row[0] for row in rows]}")
    print("Observer verification: inspect the shadow journal/cursor separately; no audio is emitted here.")


if __name__ == "__main__":
    main()
