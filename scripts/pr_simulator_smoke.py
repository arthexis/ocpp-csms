"""Exercise a genuine OCPP 1.6J charge over a private loopback CSMS.

Runs only on the trusted appliance runner. Never deploys or touches the live
CSMS service, listener, or transaction archive.
"""
from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

from ocpp_csms.transactions.query import TransactionQuery


def free_loopback_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_server(proc: subprocess.Popen, port: int, *, seconds: float = 20) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"isolated CSMS exited early (status {proc.returncode})")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.15)
    raise TimeoutError("isolated CSMS did not listen on loopback")


def main() -> None:
    simulator = Path.home() / ".local/bin/ocpp-simulator"
    if not simulator.is_file():
        raise RuntimeError(f"simulator is not installed: {simulator}")
    cp_id = f"SIM-CI-{os.getpid()}"
    with tempfile.TemporaryDirectory(prefix="ocpp-pr-integration-") as tmp:
        data = Path(tmp) / "data"
        port = free_loopback_port()
        csms_command = [
            str(Path(sys.executable).with_name("ocpp-csms")), "--data-dir", str(data),
            "serve", "--host", "127.0.0.1", "--port", str(port),
        ]
        # Temporary output files prevent subprocesses blocking on full pipes.
        with (Path(tmp) / "server.log").open("w+") as server_log:
            server = subprocess.Popen(csms_command, stdout=server_log, stderr=subprocess.STDOUT)
            try:
                wait_for_server(server, port)
                sim_command = [
                    str(simulator), "run", "--cp", cp_id,
                    "--url", f"ws://127.0.0.1:{port}/{cp_id}",
                    "--connector", "1", "--rfid", "SIM-CI",
                    "--power", "7.2", "--duration", "6", "--meter-interval", "1",
                ]
                sim = subprocess.run(sim_command, capture_output=True, text=True, timeout=50)
                print(sim.stdout, flush=True)
                if sim.returncode:
                    raise RuntimeError(f"simulator exited {sim.returncode}: {sim.stderr}")
                if "Transaction complete: 12 Wh" not in sim.stdout:
                    raise AssertionError("simulator did not complete expected 12 Wh charge")
                # Allow archive writes to settle, but do not accept another charger's results.
                deadline = time.monotonic() + 10
                while True:
                    matching = TransactionQuery(data).list(charger=cp_id)
                    if matching and matching[0].status == "stopped":
                        break
                    if time.monotonic() >= deadline:
                        raise AssertionError("no stopped transaction in isolated CSMS archive")
                    time.sleep(0.2)
                record = matching[0].record
                start, stop = record["start"], record["stop"]
                delivered = int(stop["meter_stop"]) - int(start["meter_start"])
                assert delivered == 12, f"unexpected CSMS-recorded energy: {delivered} Wh"
                assert record.get("meter_values"), "CSMS did not receive any MeterValues"
                print(f"PASS: {cp_id} transaction {matching[0].transaction_id}, {delivered} Wh")
            except BaseException:
                server_log.flush()
                server_log.seek(0)
                print("Isolated CSMS log:\n" + server_log.read(), file=sys.stderr)
                raise
            finally:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)


if __name__ == "__main__":
    main()
