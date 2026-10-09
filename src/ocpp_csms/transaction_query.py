from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ocpp_csms.transactions import default_data_dir

_ACTIVE_STATUSES = {"open", "recovered"}


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


def _connector_id(record: dict[str, Any]) -> int | None:
    start = record.get("start")
    if isinstance(start, dict) and start.get("connector_id") is not None:
        return int(start["connector_id"])
    meter_values = record.get("meter_values")
    if isinstance(meter_values, list):
        for payload in reversed(meter_values):
            if isinstance(payload, dict) and payload.get("connector_id") is not None:
                return int(payload["connector_id"])
    stop = record.get("stop")
    if isinstance(stop, dict) and stop.get("connector_id") is not None:
        return int(stop["connector_id"])
    return None


def _id_tag(record: dict[str, Any]) -> str | None:
    value = record.get("id_tag")
    if isinstance(value, str):
        return value
    for field in ("start", "stop"):
        payload = record.get(field)
        if isinstance(payload, dict) and isinstance(payload.get("id_tag"), str):
            return payload["id_tag"]
    return None


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
    """Newest charger-reported OCPP event time, with archive fallback."""
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
    return _archive_time(record)


def _archive_time(record: dict[str, Any]) -> datetime:
    for value in (record.get("updated_at"), record.get("created_at")):
        parsed = _parse_time(value)
        if parsed is not None:
            return parsed
    return datetime.min.replace(tzinfo=timezone.utc)


def _received_activity_at(record: dict[str, Any]) -> datetime:
    """Newest CSMS receive evidence, including legacy archive timestamps."""
    candidates: list[datetime] = [_archive_time(record)]
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
    unresolved: tuple[dict[str, Any], ...] = ()

    @property
    def transaction_id(self) -> int:
        return int(self.record["transaction_id"])

    @property
    def charge_point_id(self) -> str:
        return str(self.record.get("charge_point_id", ""))

    @property
    def status(self) -> str:
        return str(self.record.get("status", ""))

    @property
    def connector_id(self) -> int | None:
        return _connector_id(self.record)

    @property
    def id_tag(self) -> str | None:
        return _id_tag(self.record)

    @property
    def activity_at(self) -> datetime:
        return _activity_at(self.record)

    @property
    def received_activity_at(self) -> datetime:
        return _received_activity_at(self.record)

    def event_time(self, *, local_time: bool = False) -> datetime:
        return self.received_activity_at if local_time else self.activity_at

    @property
    def active(self) -> bool:
        return self.record.get("stop") is None and self.status in _ACTIVE_STATUSES


class TransactionQuery:
    """Read archived transaction state without mutating persistence."""

    def __init__(self, data_dir: str | Path | None = None) -> None:
        self.data_dir = Path(data_dir).expanduser() if data_dir else default_data_dir()
        self.transactions_dir = self.data_dir / "transactions"
        self.unresolved_dir = self.data_dir / "transactions-unresolved"

    def get(self, transaction_id: int) -> TransactionView | None:
        for view in self._views():
            if view.transaction_id == transaction_id:
                return view
        return None

    def list(self, *, charger: str | None = None, connector: int | None = None, id_tag: str | None = None, since: datetime | str | None = None, until: datetime | str | None = None, active: bool | None = None, limit: int | None = None, local_time: bool = False) -> list[TransactionView]:
        since_time = self._coerce_time(since)
        until_time = self._coerce_time(until)
        matches = [
            view for view in self._views()
            if (charger is None or view.charge_point_id == charger)
            and (connector is None or view.connector_id == connector)
            and (id_tag is None or view.id_tag == id_tag)
            and (active is None or view.active is active)
            and (since_time is None or view.event_time(local_time=local_time) >= since_time)
            and (until_time is None or view.event_time(local_time=local_time) <= until_time)
        ]
        matches.sort(key=lambda view: (view.event_time(local_time=local_time), view.transaction_id), reverse=True)
        return matches[: max(limit, 0)] if limit is not None else matches

    def active(self, **filters: Any) -> list[TransactionView]:
        return self.list(active=True, **filters)

    def last(self, **filters: Any) -> TransactionView | None:
        matches = self.list(limit=1, **filters)
        return matches[0] if matches else None

    def _views(self) -> Iterable[TransactionView]:
        unresolved = self._unresolved_by_transaction()
        if not self.transactions_dir.exists():
            return ()
        views: list[TransactionView] = []
        for path in self.transactions_dir.glob("*/*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                transaction_id = int(record["transaction_id"])
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                continue
            views.append(TransactionView(record=record, path=path, unresolved=tuple(unresolved.get(transaction_id, ()))))
        return views

    def _unresolved_by_transaction(self) -> dict[int, list[dict[str, Any]]]:
        grouped: dict[int, list[dict[str, Any]]] = {}
        if not self.unresolved_dir.exists():
            return grouped
        for path in self.unresolved_dir.glob("*/*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                transaction_id = int(record["transaction_id"])
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                continue
            grouped.setdefault(transaction_id, []).append(record)
        return grouped

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
