from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ocpp_csms.time import utc_now_iso

LOGGER = logging.getLogger(__name__)
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")
_START_RETRY_WINDOW = timedelta(seconds=60)
_ORIGIN_LOCAL = "local"
_ORIGIN_RECOVERED = "recovered"


def default_data_dir() -> Path:
    return Path.home() / "ocpp-csms-data"


def _transaction_date(payload: dict[str, Any]) -> str:
    timestamp = payload.get("timestamp")
    if isinstance(timestamp, str):
        try:
            return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            pass
    return datetime.now(timezone.utc).date().isoformat()


def _safe_charge_point_id(charge_point_id: str) -> str:
    cleaned = _SAFE_NAME.sub("_", charge_point_id).strip("._")
    return cleaned or "charger"


def _start_key(charge_point_id: str, payload: dict[str, Any]) -> str:
    return json.dumps([charge_point_id, payload], sort_keys=True, separators=(",", ":"), ensure_ascii=False)


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


def _payload_time(payload: dict[str, Any]) -> datetime | None:
    direct = _parse_time(payload.get("timestamp"))
    if direct is not None:
        return direct
    meter_values = payload.get("meter_value")
    if not isinstance(meter_values, list):
        return None
    timestamps = [_parse_time(entry.get("timestamp")) for entry in meter_values if isinstance(entry, dict)]
    timestamps = [value for value in timestamps if value is not None]
    return min(timestamps) if timestamps else None


def _record_origin(record: dict[str, Any]) -> str:
    origin = record.get("origin")
    if origin in {_ORIGIN_LOCAL, _ORIGIN_RECOVERED}:
        return origin
    return _ORIGIN_LOCAL if isinstance(record.get("start"), dict) else _ORIGIN_RECOVERED


def _decision(event: str, transaction_id: int, message_type: str, payload: dict[str, Any], **extra: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"event": event, "transaction_id": transaction_id, "message_type": message_type}
    if payload.get("connector_id") is not None:
        result["connector_id"] = int(payload["connector_id"])
    result.update(extra)
    return result


class TransactionArchive:
    """Human-readable, append-friendly JSON transaction persistence."""

    def __init__(self, data_dir: str | Path | None = None) -> None:
        self.data_dir = Path(data_dir).expanduser() if data_dir else default_data_dir()
        self.transactions_dir = self.data_dir / "transactions"
        self.unresolved_dir = self.data_dir / "transactions-unresolved"
        self.transactions_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._paths: dict[int, Path] = {}
        self._origins: dict[int, str] = {}
        self._recent_starts: dict[str, tuple[int, datetime]] = {}
        self._next_transaction_id = 1
        self._scan_existing()

    def _scan_existing(self) -> None:
        highest_local = 0
        for path in self.transactions_dir.glob("*/*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                transaction_id = int(record["transaction_id"])
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                continue
            origin = _record_origin(record)
            self._paths[transaction_id] = path
            self._origins[transaction_id] = origin
            if origin == _ORIGIN_LOCAL:
                highest_local = max(highest_local, transaction_id)
            start = record.get("start")
            created_at = _parse_time(record.get("created_at"))
            charge_point_id = record.get("charge_point_id")
            if isinstance(start, dict) and isinstance(charge_point_id, str) and created_at:
                key = _start_key(charge_point_id, start)
                existing = self._recent_starts.get(key)
                if existing is None or created_at > existing[1]:
                    self._recent_starts[key] = (transaction_id, created_at)
        self._next_transaction_id = highest_local + 1

    def _next_available_local_id(self) -> int:
        transaction_id = self._next_transaction_id
        while transaction_id in self._paths:
            transaction_id += 1
        return transaction_id

    async def start(self, charge_point_id: str, payload: dict[str, Any]) -> int:
        async with self._lock:
            now = datetime.now(timezone.utc)
            key = _start_key(charge_point_id, payload)
            recent = self._recent_starts.get(key)
            if recent is not None and timedelta(0) <= now - recent[1] <= _START_RETRY_WINDOW:
                return recent[0]
            transaction_id = self._next_available_local_id()
            received_at = utc_now_iso()
            record = {
                "transaction_id": transaction_id,
                "origin": _ORIGIN_LOCAL,
                "charge_point_id": charge_point_id,
                "status": "open",
                "created_at": received_at,
                "updated_at": received_at,
                "id_tag": payload.get("id_tag"),
                "start": payload,
                "start_received_at": received_at,
                "meter_values": [],
                "meter_values_received_at": [],
                "stop": None,
                "stop_received_at": None,
            }
            path = self._path_for(transaction_id, charge_point_id, payload)
            try:
                self._write(path, record)
            except Exception:
                LOGGER.exception("Could not write transaction JSON for %s transaction %s", charge_point_id, transaction_id)
                raise
            self._paths[transaction_id] = path
            self._origins[transaction_id] = _ORIGIN_LOCAL
            self._recent_starts[key] = (transaction_id, _parse_time(received_at) or now)
            self._next_transaction_id = transaction_id + 1
            return transaction_id

    async def meter_values(self, charge_point_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        async with self._lock:
            transaction_id = payload.get("transaction_id")
            if transaction_id is None:
                return None
            transaction_id = int(transaction_id)
            loaded = self._load_or_recover(transaction_id, charge_point_id, payload, message_type="MeterValues")
            if loaded[0] is None:
                return loaded[2]
            path, record, decision = loaded
            received_at = utc_now_iso()
            meter_values = record.setdefault("meter_values", [])
            received_times = record.setdefault("meter_values_received_at", [])
            while len(received_times) < len(meter_values):
                received_times.append(None)
            meter_values.append(payload)
            received_times.append(received_at)
            record["updated_at"] = received_at
            self._write(path, record)
            return decision

    async def infer_stop(
        self, transaction_id: int, *, reason: str = "disconnected_timeout",
    ) -> bool:
        """Record recovery evidence without inventing a charger stop payload."""
        if reason != "disconnected_timeout":
            raise ValueError("unsupported recovery reason")
        async with self._lock:
            path = self._paths.get(int(transaction_id))
            if path is None or not path.exists():
                return False
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("status") not in {"open", "recovered"} or record.get("stop") is not None:
                return False
            now = utc_now_iso()
            record["recovery"] = {
                "reason": reason,
                "inferred_at": now,
                "last_activity_at": record.get("updated_at", record.get("created_at")),
                "last_meter_wh": None,
                "reconciled_at": None,
            }
            record["status"] = "inferred_stopped"
            record["updated_at"] = now
            self._write(path, record)
            return True

    async def stop(self, charge_point_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        async with self._lock:
            transaction_id = int(payload["transaction_id"])
            loaded = self._load_or_recover(transaction_id, charge_point_id, payload, message_type="StopTransaction")
            if loaded[0] is None:
                return loaded[2]
            path, record, decision = loaded
            received_at = utc_now_iso()
            record["stop"] = payload
            record["stop_received_at"] = received_at
            if isinstance(record.get("recovery"), dict):
                record["recovery"]["reconciled_at"] = received_at
            record["status"] = "stopped"
            record["updated_at"] = received_at
            self._write(path, record)
            return decision

    def _load_or_recover(self, transaction_id: int, charge_point_id: str, payload: dict[str, Any], *, message_type: str) -> tuple[Path | None, dict[str, Any] | None, dict[str, Any] | None]:
        path = self._paths.get(transaction_id)
        if path is not None and path.exists():
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                origin = _record_origin(record)
                record.setdefault("origin", origin)
                self._origins[transaction_id] = origin
                reason = self._conflict_reason(record, charge_point_id, payload)
                if reason is not None:
                    unresolved_path = self._preserve_unresolved(transaction_id, charge_point_id, payload, message_type=message_type, reason=reason)
                    return None, None, _decision("historical_transaction_id_collision", transaction_id, message_type, payload, decision="preserved_unresolved", reason=reason, unresolved_path=str(unresolved_path.relative_to(self.data_dir)))
                if origin == _ORIGIN_RECOVERED:
                    return path, record, _decision("historical_transaction_evidence_attached", transaction_id, message_type, payload, decision="attached")
                return path, record, None
            except (OSError, json.JSONDecodeError):
                pass
        received_at = utc_now_iso()
        path = self._path_for(transaction_id, charge_point_id, payload)
        record = {
            "transaction_id": transaction_id,
            "origin": _ORIGIN_RECOVERED,
            "charge_point_id": charge_point_id,
            "status": "recovered",
            "created_at": received_at,
            "updated_at": received_at,
            "id_tag": payload.get("id_tag"),
            "start": None,
            "start_received_at": None,
            "meter_values": [],
            "meter_values_received_at": [],
            "stop": None,
            "stop_received_at": None,
        }
        self._paths[transaction_id] = path
        self._origins[transaction_id] = _ORIGIN_RECOVERED
        return path, record, _decision("historical_transaction_recovered", transaction_id, message_type, payload, decision="adopted")

    def _conflict_reason(self, record: dict[str, Any], charge_point_id: str, payload: dict[str, Any]) -> str | None:
        existing_charge_point = record.get("charge_point_id")
        if isinstance(existing_charge_point, str) and existing_charge_point != charge_point_id:
            return "charge_point_mismatch"
        start = record.get("start")
        if not isinstance(start, dict):
            return None
        existing_connector = start.get("connector_id")
        incoming_connector = payload.get("connector_id")
        if existing_connector is not None and incoming_connector is not None and int(existing_connector) != int(incoming_connector):
            return "connector_mismatch"
        start_time = _parse_time(start.get("timestamp"))
        incoming_time = _payload_time(payload)
        if start_time is not None and incoming_time is not None and incoming_time < start_time:
            return "message_predates_local_start"
        return None

    def _preserve_unresolved(self, transaction_id: int, charge_point_id: str, payload: dict[str, Any], *, message_type: str, reason: str) -> Path:
        timestamp = utc_now_iso()
        day_dir = self.unresolved_dir / timestamp[:10]
        day_dir.mkdir(parents=True, exist_ok=True)
        charger = _safe_charge_point_id(charge_point_id)
        path = day_dir / f"{charger}-{transaction_id}-{message_type}-{uuid.uuid4().hex[:12]}.json"
        self._write(path, {"transaction_id": transaction_id, "charge_point_id": charge_point_id, "message_type": message_type, "reason": reason, "received_at": timestamp, "payload": payload})
        return path

    def _path_for(self, transaction_id: int, charge_point_id: str, payload: dict[str, Any]) -> Path:
        day_dir = self.transactions_dir / _transaction_date(payload)
        day_dir.mkdir(parents=True, exist_ok=True)
        return day_dir / f"{_safe_charge_point_id(charge_point_id)}-{transaction_id}.json"

    def _write(self, path: Path, record: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(temporary, path)
