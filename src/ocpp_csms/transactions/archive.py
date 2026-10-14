from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ocpp_csms.time import utc_now_iso

LOGGER = logging.getLogger(__name__)
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")
_ORIGIN_LOCAL = "local"


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


class TransactionArchive:
    """Human-readable, append-friendly JSON transaction persistence."""

    def __init__(self, data_dir: str | Path | None = None) -> None:
        self.data_dir = Path(data_dir).expanduser() if data_dir else default_data_dir()
        self.transactions_dir = self.data_dir / "transactions"
        self.unresolved_dir = self.data_dir / "transactions-unresolved"
        self.transactions_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._paths: dict[int, Path] = {}
        self._scan_existing()

    def _scan_existing(self) -> None:
        """Index existing diagnostic files without deriving transaction identity."""
        for path in self.transactions_dir.glob("*/*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                transaction_id = int(record["transaction_id"])
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                continue
            self._paths[transaction_id] = path

    async def diagnostic_start(
        self, charge_point_id: str, payload: dict[str, Any], transaction_id: int,
    ) -> None:
        """Optional JSON projection of a committed SQLite start; never allocate an ID."""
        async with self._lock:
            existing = self._paths.get(transaction_id)
            if existing is not None and existing.exists():
                return
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
            self._write(path, record)
            self._paths[transaction_id] = path

    async def meter_values(self, charge_point_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        async with self._lock:
            transaction_id = payload.get("transaction_id")
            if transaction_id is None:
                return None
            transaction_id = int(transaction_id)
            loaded = self._load_existing(transaction_id, charge_point_id, payload, message_type="MeterValues")
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

    async def stop(self, charge_point_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        async with self._lock:
            transaction_id = int(payload["transaction_id"])
            loaded = self._load_existing(transaction_id, charge_point_id, payload, message_type="StopTransaction")
            if loaded[0] is None:
                return loaded[2]
            path, record, decision = loaded
            received_at = utc_now_iso()
            record["stop"] = payload
            record["stop_received_at"] = received_at
            record["status"] = "stopped"
            record["updated_at"] = received_at
            self._write(path, record)
            return decision

    def _load_existing(self, transaction_id: int, charge_point_id: str, payload: dict[str, Any], *, message_type: str) -> tuple[Path | None, dict[str, Any] | None, dict[str, Any] | None]:
        """Only use an existing diagnostic record; never reconstruct a transaction."""
        path = self._paths.get(transaction_id)
        if path is None or not path.exists():
            return None, None, None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None, None, None
        reason = self._conflict_reason(record, charge_point_id, payload)
        if reason is not None:
            # Authoritative orphan/conflicting evidence is already in SQLite.
            return None, None, None
        return path, record, None

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
