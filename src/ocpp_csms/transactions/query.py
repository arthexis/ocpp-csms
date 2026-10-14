from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ocpp_csms.transactions.archive import default_data_dir
from ocpp_csms.schema import database_path

_ACTIVE_STATUSES = {"open"}


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _meter_times(record: dict[str, Any]) -> list[datetime]:
    times: list[datetime] = []
    payloads = record.get("meter_values")
    if not isinstance(payloads, list):
        return times
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        meter_values = payload.get("meter_value")
        if not isinstance(meter_values, list):
            continue
        for entry in meter_values:
            if isinstance(entry, dict):
                parsed = _parse_time(entry.get("timestamp"))
                if parsed is not None:
                    times.append(parsed)
    return times


def _activity_at(record: dict[str, Any]) -> datetime:
    """Newest charger-reported OCPP event time, falling back to SQLite receipt."""
    candidates: list[datetime] = []
    stop = record.get("stop")
    if isinstance(stop, dict):
        parsed = _parse_time(stop.get("timestamp"))
        if parsed is not None:
            candidates.append(parsed)
    candidates.extend(_meter_times(record))
    start = record.get("start")
    if isinstance(start, dict):
        parsed = _parse_time(start.get("timestamp"))
        if parsed is not None:
            candidates.append(parsed)
    if candidates:
        return max(candidates)
    return _sqlite_activity_time(record)


def _sqlite_activity_time(record: dict[str, Any]) -> datetime:
    for value in (record.get("updated_at"), record.get("created_at")):
        parsed = _parse_time(value)
        if parsed is not None:
            return parsed
    return datetime.min.replace(tzinfo=timezone.utc)


def _received_activity_at(record: dict[str, Any]) -> datetime:
    """Newest SQLite receive evidence, independent of diagnostic archives."""
    candidates: list[datetime] = [_sqlite_activity_time(record)]
    for field in ("start_received_at", "stop_received_at"):
        parsed = _parse_time(record.get(field))
        if parsed is not None:
            candidates.append(parsed)
    meter_times = record.get("meter_values_received_at")
    if isinstance(meter_times, list):
        for value in meter_times:
            parsed = _parse_time(value)
            if parsed is not None:
                candidates.append(parsed)
    return max(candidates)


@dataclass(frozen=True)
class TransactionView:
    record: dict[str, Any]
    path: Path

    @property
    def transaction_id(self) -> int:
        return int(self.record["transaction_id"])

    @property
    def charge_point_id(self) -> str:
        return str(self.record.get("charge_point_id", ""))

    @property
    def status(self) -> str:
        state = str(self.record.get("status", ""))
        return state

    @property
    def connector_id(self) -> int | None:
        value = self.record.get("connector_id")
        return int(value) if value is not None else None

    @property
    def id_tag(self) -> str | None:
        value = self.record.get("id_tag")
        return str(value) if value is not None else None

    @property
    def activity_at(self) -> datetime:
        return _activity_at(self.record)

    @property
    def received_activity_at(self) -> datetime:
        return _received_activity_at(self.record)

    def event_time(self, *, local_time: bool = False) -> datetime:
        return self.received_activity_at if local_time else self.activity_at

    @property
    def next_start_received_at(self) -> str | None:
        return self.record.get("next_start_received_at")

    @property
    def active(self) -> bool:
        return self.record.get("stop_received_at") is None and self.status in _ACTIVE_STATUSES


class TransactionQuery:
    """Read authoritative SQLite transaction state without mutating persistence."""

    def __init__(self, data_dir: str | Path | None = None, *, connection: sqlite3.Connection | None = None) -> None:
        self.data_dir = Path(data_dir).expanduser() if data_dir else default_data_dir()
        self._connection = connection

    def get(self, transaction_id: int) -> TransactionView | None:
        views = self._views(transaction_id=transaction_id)
        return views[0] if views else None

    def list(self, *, charger: str | None = None, connector: int | None = None, id_tag: str | None = None, since: datetime | str | None = None, until: datetime | str | None = None, active: bool | None = None, limit: int | None = None, local_time: bool = False) -> list[TransactionView]:
        since_time = self._coerce_time(since)
        until_time = self._coerce_time(until)
        matches = [
            view for view in self._views(charger=charger, connector=connector, id_tag=id_tag, active=active)
            if (since_time is None or view.event_time(local_time=local_time) >= since_time)
            and (until_time is None or view.event_time(local_time=local_time) <= until_time)
        ]
        matches.sort(key=lambda view: (view.event_time(local_time=local_time), view.transaction_id), reverse=True)
        return matches[: max(limit, 0)] if limit is not None else matches

    def active(self, **filters: Any) -> list[TransactionView]:
        return self.list(active=True, **filters)

    def last(self, **filters: Any) -> TransactionView | None:
        matches = self.list(limit=1, **filters)
        return matches[0] if matches else None

    def _views(self, *, transaction_id: int | None = None, charger: str | None = None,
               connector: int | None = None, id_tag: str | None = None,
               active: bool | None = None) -> list[TransactionView]:
        """Filter summaries before reading evidence; preserve event-time ordering."""
        path = database_path(self.data_dir)
        if not path.is_file():
            return []
        predicates: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("transaction_id", transaction_id), ("charger_id", charger),
            ("connector_id", connector), ("id_tag", id_tag),
        ):
            if value is not None:
                predicates.append(f"{column} = ?")
                params.append(value)
        if active is not None:
            predicates.append("state = 'open' AND stop_received_at IS NULL" if active
                              else "(state != 'open' OR stop_received_at IS NOT NULL)")
        where = (" WHERE " + " AND ".join(predicates)) if predicates else ""
        connection = self._connection or sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            connection.row_factory = sqlite3.Row
            rows = connection.execute("SELECT * FROM transaction_summary" + where, params).fetchall()
            if not rows:
                return []
            keys = sorted((int(row["transaction_id"]), str(row["charger_id"])) for row in rows)
            evidence: list[sqlite3.Row] = []
            # Bound parameter count for unlimited listings.
            for offset in range(0, len(keys), 400):
                batch = keys[offset:offset + 400]
                placeholders = " OR ".join("(transaction_id = ? AND charger_id = ?)" for _ in batch)
                values = [item for key in batch for item in key]
                evidence.extend(connection.execute(
                    """SELECT id, transaction_id, charger_id, action, payload_json, received_at
                       FROM events WHERE direction = 'in'
                         AND action IN ('StartTransaction', 'MeterValues', 'StopTransaction')
                         AND (""" + placeholders + ") ORDER BY id",
                    values,
                ).fetchall())
            evidence.sort(key=lambda event: event["id"])
        finally:
            if self._connection is None:
                connection.close()

        grouped: dict[tuple[int, str], list[sqlite3.Row]] = {}
        for event in evidence:
            grouped.setdefault((int(event["transaction_id"]), str(event["charger_id"])), []).append(event)

        views: list[TransactionView] = []
        for row in rows:
            transaction_id = int(row["transaction_id"])
            record: dict[str, Any] = {
                "transaction_id": transaction_id,
                "charge_point_id": row["charger_id"],
                "status": row["state"],
                "id_tag": row["id_tag"],
                "start_received_at": row["start_received_at"],
                "stop_received_at": row["stop_received_at"],
                "created_at": row["start_received_at"],
                "updated_at": row["last_activity_at"],
                "connector_id": row["connector_id"],
                "energy_wh": row["energy_wh"],
                "next_start_received_at": row["next_start_received_at"],
                "start": None,
                "stop": None,
                "meter_values": [],
                "meter_values_received_at": [],
            }
            for event in grouped.get((transaction_id, str(row["charger_id"])), ()):
                try:
                    payload = json.loads(event["payload_json"])
                except (TypeError, ValueError):
                    continue
                if not isinstance(payload, dict):
                    continue
                if event["action"] == "StartTransaction" and record["start"] is None:
                    record["start"] = payload
                elif event["action"] == "StopTransaction":
                    # The first committed stop is authoritative, not a later retry.
                    if event["received_at"] == row["stop_received_at"] and record["stop"] is None:
                        record["stop"] = payload
                elif event["action"] == "MeterValues":
                    record["meter_values"].append(payload)
                    record["meter_values_received_at"].append(event["received_at"])
            if record["start"] is None:
                record["start"] = {
                    "connector_id": row["connector_id"],
                    "id_tag": row["id_tag"],
                    "meter_start": row["meter_start"],
                    "timestamp": row["started_at"],
                }
            if row["stop_received_at"] is not None and record["stop"] is None:
                record["stop"] = {
                    "transaction_id": transaction_id,
                    "meter_stop": row["meter_stop"],
                    "timestamp": row["stopped_at"],
                }
            views.append(TransactionView(record=record, path=path))
        return views

    @staticmethod
    def _coerce_time(value: datetime | str | None) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        parsed = _parse_time(value)
        if parsed is None:
            raise ValueError(f"Invalid timestamp: {value}")
        return parsed
