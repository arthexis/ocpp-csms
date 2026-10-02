from __future__ import annotations

import asyncio
import json
import logging
import os
import re
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
    """Return the user-owned default data directory for the appliance."""
    return Path.home() / "ocpp-csms-data"


def _transaction_date(payload: dict[str, Any]) -> str:
    timestamp = payload.get("timestamp")
    if isinstance(timestamp, str):
        try:
            parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            return parsed.date().isoformat()
        except ValueError:
            pass
    return datetime.now(timezone.utc).date().isoformat()


def _safe_charge_point_id(charge_point_id: str) -> str:
    cleaned = _SAFE_NAME.sub("_", charge_point_id).strip("._")
    return cleaned or "charger"


def _start_key(charge_point_id: str, payload: dict[str, Any]) -> str:
    return json.dumps(
        [charge_point_id, payload],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


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


def _record_origin(record: dict[str, Any]) -> str:
    """Return explicit provenance, inferring it for pre-origin archives."""
    origin = record.get("origin")
    if origin in {_ORIGIN_LOCAL, _ORIGIN_RECOVERED}:
        return origin
    return _ORIGIN_LOCAL if isinstance(record.get("start"), dict) else _ORIGIN_RECOVERED


class TransactionArchive:
    """Human-readable, append-friendly JSON transaction persistence."""

    def __init__(self, data_dir: str | Path | None = None) -> None:
        self.data_dir = Path(data_dir).expanduser() if data_dir else default_data_dir()
        self.transactions_dir = self.data_dir / "transactions"
        self.transactions_dir.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._paths: dict[int, Path] = {}
        self._origins: dict[int, str] = {}
        self._recent_starts: dict[str, tuple[int, datetime]] = {}
        self._next_transaction_id = 1
        self._scan_existing()

    def _scan_existing(self) -> None:
        highest = 0
        for path in self.transactions_dir.glob("*/*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                transaction_id = int(record["transaction_id"])
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                continue
            self._paths[transaction_id] = path
            self._origins[transaction_id] = _record_origin(record)
            highest = max(highest, transaction_id)

            start = record.get("start")
            created_at = _parse_time(record.get("created_at"))
            charge_point_id = record.get("charge_point_id")
            if isinstance(start, dict) and isinstance(charge_point_id, str) and created_at:
                key = _start_key(charge_point_id, start)
                existing = self._recent_starts.get(key)
                if existing is None or created_at > existing[1]:
                    self._recent_starts[key] = (transaction_id, created_at)
        self._next_transaction_id = highest + 1

    async def start(self, charge_point_id: str, payload: dict[str, Any]) -> int:
        async with self._lock:
            now = datetime.now(timezone.utc)
            key = _start_key(charge_point_id, payload)
            recent = self._recent_starts.get(key)
            if recent is not None:
                age = now - recent[1]
                if timedelta(0) <= age <= _START_RETRY_WINDOW:
                    return recent[0]

            transaction_id = self._next_transaction_id
            timestamp = utc_now_iso()
            record = {
                "transaction_id": transaction_id,
                "origin": _ORIGIN_LOCAL,
                "charge_point_id": charge_point_id,
                "status": "open",
                "created_at": timestamp,
                "updated_at": timestamp,
                "id_tag": payload.get("id_tag"),
                "start": payload,
                "meter_values": [],
                "stop": None,
            }
            path = self._path_for(transaction_id, charge_point_id, payload)
            try:
                self._write(path, record)
            except Exception:
                LOGGER.exception(
                    "Could not write transaction JSON for %s transaction %s",
                    charge_point_id,
                    transaction_id,
                )
                raise

            self._paths[transaction_id] = path
            self._origins[transaction_id] = _ORIGIN_LOCAL
            self._recent_starts[key] = (transaction_id, _parse_time(timestamp) or now)
            self._next_transaction_id += 1
            return transaction_id

    async def meter_values(
        self,
        charge_point_id: str,
        payload: dict[str, Any],
    ) -> None:
        async with self._lock:
            transaction_id = payload.get("transaction_id")
            if transaction_id is None:
                return
            transaction_id = int(transaction_id)
            path, record = self._load_or_recover(transaction_id, charge_point_id, payload)
            record.setdefault("meter_values", []).append(payload)
            record["updated_at"] = utc_now_iso()
            self._write(path, record)

    async def stop(self, charge_point_id: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            transaction_id = int(payload["transaction_id"])
            path, record = self._load_or_recover(transaction_id, charge_point_id, payload)
            record["stop"] = payload
            record["status"] = "stopped"
            record["updated_at"] = utc_now_iso()
            self._write(path, record)

    def _load_or_recover(
        self,
        transaction_id: int,
        charge_point_id: str,
        payload: dict[str, Any],
    ) -> tuple[Path, dict[str, Any]]:
        path = self._paths.get(transaction_id)
        if path is not None and path.exists():
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                origin = _record_origin(record)
                record.setdefault("origin", origin)
                self._origins[transaction_id] = origin
                return path, record
            except (OSError, json.JSONDecodeError):
                pass

        path = self._path_for(transaction_id, charge_point_id, payload)
        record = {
            "transaction_id": transaction_id,
            "origin": _ORIGIN_RECOVERED,
            "charge_point_id": charge_point_id,
            "status": "recovered",
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
            "id_tag": payload.get("id_tag"),
            "start": None,
            "meter_values": [],
            "stop": None,
        }
        self._paths[transaction_id] = path
        self._origins[transaction_id] = _ORIGIN_RECOVERED
        self._next_transaction_id = max(self._next_transaction_id, transaction_id + 1)
        return path, record

    def _path_for(
        self,
        transaction_id: int,
        charge_point_id: str,
        payload: dict[str, Any],
    ) -> Path:
        day_dir = self.transactions_dir / _transaction_date(payload)
        day_dir.mkdir(parents=True, exist_ok=True)
        charger = _safe_charge_point_id(charge_point_id)
        return day_dir / f"{charger}-{transaction_id}.json"

    def _write(self, path: Path, record: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
