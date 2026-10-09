"""Conservative, restart-safe recovery of disconnected OCPP transactions.

Never infer a physical stop: eligibility requires a persisted disconnect event
and inactivity evidence older than the configured timeout.
"""
from __future__ import annotations

import asyncio
import logging
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timedelta, timezone

from ocpp_csms.transaction_query import TransactionQuery

LOGGER = logging.getLogger(__name__)
TIMEOUT_SECONDS = 3600
SCAN_SECONDS = 300
POLICY_NAME = "recovery-policy.json"


@dataclass(frozen=True)
class RecoveryPolicy:
    enabled: bool = True
    timeout_seconds: int = TIMEOUT_SECONDS
    interval_seconds: int = SCAN_SECONDS


def read_policy(data_dir: str | Path) -> RecoveryPolicy:
    path = Path(data_dir) / POLICY_NAME
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return RecoveryPolicy()
    if not isinstance(value, dict) or set(value) != {"enabled", "timeout_seconds", "interval_seconds"}:
        raise ValueError("invalid recovery policy fields")
    if type(value["enabled"]) is not bool:
        raise ValueError("recovery enabled must be a boolean")
    for field in ("timeout_seconds", "interval_seconds"):
        if type(value[field]) is not int or value[field] < 1:
            raise ValueError(f"{field} must be a positive integer")
    return RecoveryPolicy(**value)


def write_policy(data_dir: str | Path, policy: RecoveryPolicy) -> None:
    path = Path(data_dir) / POLICY_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    import tempfile
    fd, temp = tempfile.mkstemp(prefix=".recovery-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(vars(policy), handle, sort_keys=True)
            handle.write("\\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _time(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _disconnections(events) -> dict[str, datetime]:
    """Latest persisted connection transition per charger; no stale PID reliance."""
    with sqlite3.connect(f"file:{events.path}?mode=ro", uri=True) as db:
        rows = db.execute(
            """SELECT charger_id, event, occurred_at FROM runtime_events
               WHERE charger_id IS NOT NULL AND event IN
               ('charger_connected', 'charger_disconnected') ORDER BY id"""
        ).fetchall()
    latest: dict[str, tuple[str, datetime]] = {}
    for charger, event, when in rows:
        latest[str(charger)] = (str(event), _time(str(when)))
    return {charger: when for charger, (event, when) in latest.items()
            if event == "charger_disconnected"}


async def recover_once(server, *, timeout_seconds: int = TIMEOUT_SECONDS,
                       now: datetime | None = None, charger: str | None = None, dry_run: bool = False) -> list[int]:
    """Scan historical evidence; repeat safely after restart or partial writes."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must have timezone")
    recovered = []
    charger_filter = charger
    disconnected = _disconnections(server.events)
    # In-process connection registry overrides persisted disconnect evidence.
    connected = set(server.connected_chargers())
    with sqlite3.connect(f"file:{server.events.path}?mode=ro", uri=True) as db:
        rows = db.execute(
            """SELECT transaction_id, charger_id, state, last_activity_at
               FROM transactions WHERE state IN ('open','recovered','inferred_stopped')"""
        ).fetchall()
    query = TransactionQuery(server.transactions.data_dir)
    for transaction_id, charger, state, activity_at in rows:
        if (charger_filter is not None and charger != charger_filter) or charger in connected or charger not in disconnected:
            continue
        view = query.get(int(transaction_id))
        if view is None or view.charge_point_id != charger:
            LOGGER.error("Recovery skipped transaction %s: archive mismatch", transaction_id)
            continue
        if view.status not in {"open", "recovered", "inferred_stopped"} or view.record.get("stop") is not None:
            continue
        if state == "inferred_stopped" and view.status == "inferred_stopped":
            continue
        # A disconnected charger can keep charging; this is an administrative
        # inference only. Use receive-side evidence, not untrusted device time.
        archive_activity = (view.record.get("recovery", {}).get("last_activity_at")
                            if view.status == "inferred_stopped" else None)
        last_seen = max(_time(str(activity_at)),
                        _time(archive_activity) if archive_activity else view.received_activity_at,
                        disconnected[charger])
        if now - last_seen < timedelta(seconds=timeout_seconds):
            continue
        if dry_run:
            recovered.append(int(transaction_id))
            continue
        try:
            # Archive first. If SQLite fails, a later scan repairs that state.
            if view.status in {"open", "recovered"}:
                await server.transactions.infer_stop(int(transaction_id))
            if state in {"open", "recovered"}:
                server.events.infer_transaction_stop(int(transaction_id))
            recovered.append(int(transaction_id))
        except Exception:
            LOGGER.exception("Recovery of transaction %s incomplete; will retry", transaction_id)
    return recovered


async def recovery_loop(server, *, interval_seconds: int = SCAN_SECONDS,
                        timeout_seconds: int = TIMEOUT_SECONDS) -> None:
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    while True:
        delay = interval_seconds
        try:
            policy = read_policy(server.events.data_dir)
            delay = policy.interval_seconds
            if policy.enabled:
                await recover_once(server, timeout_seconds=policy.timeout_seconds)
        except Exception:
            LOGGER.exception("Transaction recovery scan failed; retrying")
        await asyncio.sleep(delay)
