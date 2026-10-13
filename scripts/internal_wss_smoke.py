"""Read-only setup, authenticated loopback WSS smoke test of an installed CSMS.

Does not start services, modify TLS/auth, open firewall ports, or use public DNS.
Requires an already enabled native TLS listener and a pre-enrolled Basic CP identity.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from datetime import datetime, timezone
import getpass
import json
from pathlib import Path
import socket
import sqlite3
import ssl
import time

import websockets


def event_rows(db: Path, cp: str, after: int) -> list[tuple[int, str]]:
    with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=5) as connection:
        return connection.execute(
            "SELECT id, action FROM events WHERE charger_id = ? AND id > ? ORDER BY id",
            (cp, after),
        ).fetchall()


def last_event_id(db: Path) -> int:
    with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=5) as connection:
        return int(connection.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0])


def transport(hostname: str, ca_file: Path) -> ssl.SSLContext:
    """Trust only the explicit CA/certificate and verify its DNS identity."""
    return ssl.create_default_context(cafile=str(ca_file))


def basic_header(cp: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{cp}:{password}".encode()).decode("ascii")
    return {"Authorization": "Basic " + token}


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


async def exchange(ws, action: str, payload: dict, seq: int) -> dict:
    identifier = f"smoke-{seq}"
    await ws.send(json.dumps([2, identifier, action, payload]))
    frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
    if not isinstance(frame, list) or len(frame) < 3 or frame[1] != identifier:
        raise AssertionError(f"unexpected OCPP reply to {action}")
    if frame[0] != 3:
        raise AssertionError(f"OCPP {action} returned an error frame")
    return frame[2]


async def run_transaction(cp: str, password: str, hostname: str, port: int, context: ssl.SSLContext) -> int:
    # Override socket destination, not URI hostname: TLS still validates the
    # configured SAN and SNI. Connections never leave the loopback interface.
    url = f"wss://{hostname}:{port}/{cp}"
    async with websockets.connect(
        url, host="127.0.0.1", port=port,
        ssl=context, extra_headers=basic_header(cp, password),
        subprotocols=["ocpp1.6"], open_timeout=10,
    ) as ws:
        if ws.subprotocol != "ocpp1.6":
            raise AssertionError("OCPP 1.6 subprotocol not negotiated")
        boot = await exchange(ws, "BootNotification", {
            "chargePointVendor": "InternalSmoke", "chargePointModel": "WSS"}, 1)
        if boot.get("status") != "Accepted":
            raise AssertionError("BootNotification rejected")
        heartbeat = await exchange(ws, "Heartbeat", {}, 2)
        if "currentTime" not in heartbeat:
            raise AssertionError("Heartbeat missing currentTime")
        auth = await exchange(ws, "Authorize", {"idTag": "SIM-WSS-TEST"}, 3)
        if auth.get("idTagInfo", {}).get("status") != "Accepted":
            raise AssertionError("RFID not authorized")
        start = await exchange(ws, "StartTransaction", {
            "connectorId": 1, "idTag": "SIM-WSS-TEST", "meterStart": 100,
            "timestamp": timestamp()}, 4)
        tx = start.get("transactionId")
        if not isinstance(tx, int):
            raise AssertionError("StartTransaction missing integer transactionId")
        meter = await exchange(ws, "MeterValues", {
            "connectorId": 1, "transactionId": tx,
            "meterValue": [{"timestamp": timestamp(),
                            "sampledValue": [{"value": "120"}]}]}, 5)
        if meter != {}:
            raise AssertionError("unexpected MeterValues response")
        stop = await exchange(ws, "StopTransaction", {
            "transactionId": tx, "meterStop": 130, "timestamp": timestamp()}, 6)
        if stop != {}:
            raise AssertionError("unexpected StopTransaction response")
        return tx


def main() -> None:
    parser = argparse.ArgumentParser(description="Authenticated internal WSS transaction smoke test")
    parser.add_argument("--cp", required=True, help="Existing, pre-enrolled Basic-auth charge point ID")
    parser.add_argument("--tls-hostname", required=True, help="DNS identity in server certificate SAN")
    parser.add_argument("--ca-file", required=True, type=Path, help="Trusted test CA or server certificate PEM")
    parser.add_argument("--port", type=int, default=9443)
    parser.add_argument("--data-dir", type=Path, default=Path.home() / "ocpp-csms-data")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or args.cp in {".", ".."} or not args.cp or "/" in args.cp or "?" in args.cp:
        parser.error("invalid port or charge point ID")
    if not args.ca_file.is_file():
        parser.error("trusted certificate file not found")
    db = args.data_dir.expanduser() / "ocpp-csms.sqlite3"
    if not db.is_file():
        parser.error(f"database missing: {db}")
    cursor = last_event_id(db)
    with socket.create_connection(("127.0.0.1", args.port), timeout=3):
        pass
    password = getpass.getpass("Existing Basic-auth password (not echoed): ")
    if not password:
        parser.error("empty password")
    try:
        tx = asyncio.run(run_transaction(args.cp, password, args.tls_hostname,
                                         args.port, transport(args.tls_hostname, args.ca_file)))
    finally:
        password = ""
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        events = {action for _, action in event_rows(db, args.cp, cursor)}
        if {"StartTransaction", "StopTransaction"} <= events:
            print(f"PASS: WSS/OCPP 1.6J transaction {tx}; persistent events confirmed for {args.cp}")
            return
        time.sleep(0.25)
    raise AssertionError("transaction events not persisted after authenticated WSS session")


if __name__ == "__main__":
    main()
