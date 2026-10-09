"""Conservative, restart-safe recovery of disconnected OCPP transactions.

Never infer a physical stop: eligibility requires a persisted disconnect event
and inactivity evidence older than the configured timeout.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from ocpp_csms.transaction_query import TransactionQuery

LOGGER = logging.getLogger(__name__)
TIMEOUT_SECONDS = 3600
SCAN_SECONDS = 300


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
                       now: datetime | None = None) -> list[int]:
    """Scan historical evidence; repeat safely after restart or partial writes."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must have timezone")
    recovered = []
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
        if charger in connected or charger not in disconnected:
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
        try:
            await recover_once(server, timeout_seconds=timeout_seconds)
        except Exception:
            LOGGER.exception("Transaction recovery scan failed; retrying")
        await asyncio.sleep(interval_seconds)
