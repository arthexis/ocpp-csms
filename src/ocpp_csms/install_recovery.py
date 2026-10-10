"""Opt-in deploy bootstrap recovery, before the normal read-only safety gate.

Must execute without any live CSMS process. A recovered OCPP transaction is an
administrative inference, never proof of cessation of physical charging.
"""
from __future__ import annotations
import argparse
import asyncio
from pathlib import Path
from types import SimpleNamespace

from ocpp_csms.evidence.store import EventStore
from ocpp_csms.install_cutover import apply_schema_action, verify_schema_integrity
from ocpp_csms.install_preflight import evaluate_preflight
from ocpp_csms.recovery import recover_once, read_policy
from ocpp_csms.runtime import process_is_running
from ocpp_csms.schema import inspect_schema
from ocpp_csms.transactions.archive import TransactionArchive


def bootstrap_recovery(data_dir: str | Path) -> list[int]:
    root = Path(data_dir).expanduser()
    # Fail before touching schema, archives, or application state.
    if process_is_running(root):
        raise RuntimeError("CSMS is running; stop it before deploy --recover")
    prior = evaluate_preflight(root)
    if prior.connected_chargers:
        raise RuntimeError("recorded active charger connections; recovery refused")
    if prior.reason and "disagrees" in prior.reason:
        raise RuntimeError("SQLite/archive disagree; recovery refused")
    if not inspect_schema(root).exists:
        return []
    apply_schema_action(root)  # includes SQLite-safe backup for older schemas
    verify_schema_integrity(root)
    events = EventStore(root)
    archive = TransactionArchive(root)
    server = SimpleNamespace(
        events=events, transactions=archive,
        connected_chargers=lambda: [],
    )
    policy = read_policy(root)
    # No altered inactivity threshold, no force override.
    ids = asyncio.run(recover_once(server, timeout_seconds=policy.timeout_seconds))
    after = evaluate_preflight(root)
    if not after.allowed:
        raise RuntimeError(f"transactions still block deployment: {after.reason}; {after.active_chargers}")
    return ids


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Opt-in offline deployment recovery")
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args(argv)
    try:
        recovered = bootstrap_recovery(args.data_dir)
    except (RuntimeError, ValueError, OSError) as exc:
        parser.exit(1, f"Recovery refused: {exc}\\n")
    print(f"Recovery preflight passed; recovered {len(recovered)} transaction(s): {recovered}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
