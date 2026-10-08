from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ocpp_csms.energy_query import _entry_sample
from ocpp_csms.event_contract import event_record
from ocpp_csms.output import json_command_result
from ocpp_csms.schema import DATABASE_FILENAME, source_id
from ocpp_csms.status import appliance_status
from ocpp_csms.status_contract import status_contract
from ocpp_csms.transaction_contract import transaction_item
from ocpp_csms.transaction_query import TransactionQuery

SCHEMA = "ocpp-csms/export/v1"


def _event_rows(data_dir: str | Path, *, after: int, limit: int) -> tuple[list[sqlite3.Row], bool]:
    database = Path(data_dir).expanduser() / DATABASE_FILENAME
    if not database.exists():
        return [], False
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT id, received_at AS occurred_at, charger_id, 'ocpp' AS kind,
                   action, direction, transaction_id, id_tag, payload_json AS payload
            FROM events
            WHERE id > ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (after, limit + 1),
        ).fetchall()
    finally:
        connection.close()
    return rows[:limit], len(rows) > limit


def _energy_samples(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for row in rows:
        if row["direction"] != "in" or row["action"] != "MeterValues":
            continue
        try:
            payload = json.loads(row["payload"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        connector = payload.get("connector_id")
        try:
            connector_id = int(connector) if connector is not None else None
        except (TypeError, ValueError):
            connector_id = None
        transaction_id = row["transaction_id"]
        if transaction_id is None:
            try:
                transaction_id = int(payload["transaction_id"])
            except (KeyError, TypeError, ValueError):
                transaction_id = None
        entries = payload.get("meter_value")
        if not isinstance(entries, list):
            continue
        for sample_index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            sample = _entry_sample(
                charger_id=str(row["charger_id"]),
                connector_id=connector_id,
                transaction_id=int(transaction_id) if transaction_id is not None else None,
                entry=entry,
            )
            if sample is None:
                continue
            samples.append(
                {
                    "source_event_id": int(row["id"]),
                    "sample_index": sample_index,
                    "at": sample.at,
                    "charger_id": sample.charger_id,
                    "connector_id": sample.connector_id,
                    "transaction_id": sample.transaction_id,
                    "power_w": sample.power_w,
                    "energy_wh": sample.energy_wh,
                }
            )
    return samples


def export_contract(
    data_dir: str | Path,
    *,
    after: int = 0,
    limit: int = 500,
) -> dict[str, Any]:
    """Build one cursor-addressed replication page from local CSMS state."""
    if after < 0:
        raise ValueError("--after must be zero or greater")
    if limit < 1:
        raise ValueError("--limit must be at least 1")

    rows, more = _event_rows(data_dir, after=after, limit=limit)
    next_cursor = int(rows[-1]["id"]) if rows else after

    transaction_ids = sorted(
        {
            int(row["transaction_id"])
            for row in rows
            if row["transaction_id"] is not None
        }
    )
    query = TransactionQuery(data_dir)
    transactions = []
    for transaction_id in transaction_ids:
        view = query.get(transaction_id)
        if view is not None:
            transactions.append(transaction_item(view, local_time=True))

    status = status_contract(appliance_status(data_dir))["data"]
    data = {
        "source_id": source_id(data_dir),
        "cursor": {
            "after": after,
            "next": next_cursor,
            "more": more,
        },
        "events": [event_record(row) for row in rows],
        "energy": _energy_samples(rows),
        "transactions": transactions,
        "status": status,
    }
    return json_command_result(data, schema=SCHEMA)
