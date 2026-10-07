from __future__ import annotations

import logging
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable

from ocpp_forwarder.collector import CollectorClient
from ocpp_forwarder.exporter import read_export
from ocpp_forwarder.state import ForwarderState, StateStore

LOGGER = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _event_rows(
    satellite_id: str,
    source_id: str,
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "satellite_id": satellite_id,
            "source_id": source_id,
            "source_event_id": int(item["id"]),
            "at": item["at"],
            "charger_id": item.get("charger_id"),
            "connector_id": item.get("connector_id"),
            "transaction_id": item.get("transaction_id"),
            "rfid": item.get("rfid"),
            "kind": item["kind"],
            "action": item.get("action"),
            "status": item.get("status"),
            "error_code": item.get("error_code"),
            "event": item,
        }
        for item in events
    ]


def _energy_rows(
    satellite_id: str,
    source_id: str,
    samples: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {"satellite_id": satellite_id, "source_id": source_id, **item}
        for item in samples
    ]


def _transaction_rows(
    satellite_id: str,
    source_id: str,
    transactions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "satellite_id": satellite_id,
            "source_id": source_id,
            "transaction_id": item["id"],
            "charger_id": item["charger_id"],
            "connector_id": item.get("connector_id"),
            "rfid": item.get("rfid"),
            "state": item.get("state"),
            "active": bool(item.get("active")),
            "started_at": item.get("started_at"),
            "stopped_at": item.get("stopped_at"),
            "meter_start_wh": item.get("meter_start_wh"),
            "meter_stop_wh": item.get("meter_stop_wh"),
            "energy_wh": item.get("energy_wh"),
            "last_activity_at": item.get("last_activity_at"),
        }
        for item in transactions
    ]


def _charger_rows(
    satellite_id: str,
    source_id: str,
    status: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        {
            "satellite_id": satellite_id,
            "source_id": source_id,
            "charger_id": item["id"],
            "connected": bool(item.get("connected")),
            "connected_at": item.get("connected_at"),
            "last_seen": item.get("last_seen"),
            "protocol": item.get("protocol"),
            "status": item.get("status"),
            "error_code": item.get("error_code"),
        }
        for item in status.get("chargers", [])
    ]


class Forwarder:
    def __init__(
        self,
        *,
        satellite_id: str,
        csms_command: str,
        data_dir: str,
        batch_size: int,
        state_store: StateStore,
        collector: CollectorClient,
        export_reader: Callable[..., dict[str, Any]] = read_export,
    ) -> None:
        self.satellite_id = satellite_id
        self.csms_command = csms_command
        self.data_dir = data_dir
        self.batch_size = batch_size
        self.state_store = state_store
        self.collector = collector
        self.export_reader = export_reader

    def forward_once(self) -> tuple[ForwarderState, bool]:
        state = self.state_store.load()
        page = self.export_reader(
            self.csms_command,
            data_dir=self.data_dir,
            after=state.cursor,
            limit=self.batch_size,
        )
        source_id = str(page["source_id"])
        if state.source_id is not None and state.source_id != source_id:
            LOGGER.warning(
                "OCPP CSMS source changed from %s to %s; restarting cursor at zero",
                state.source_id,
                source_id,
            )
            state = ForwarderState(source_id=source_id, cursor=0)
            page = self.export_reader(
                self.csms_command,
                data_dir=self.data_dir,
                after=0,
                limit=self.batch_size,
            )
        elif state.source_id is None:
            state = replace(state, source_id=source_id)

        cursor = page["cursor"]
        events = _event_rows(self.satellite_id, source_id, page.get("events", []))
        energy = _energy_rows(self.satellite_id, source_id, page.get("energy", []))
        transactions = _transaction_rows(
            self.satellite_id, source_id, page.get("transactions", [])
        )
        chargers = _charger_rows(
            self.satellite_id, source_id, page.get("status", {})
        )

        self.collector.upsert(
            "events",
            events,
            on_conflict="satellite_id,source_id,source_event_id",
        )
        self.collector.upsert(
            "energy_samples",
            energy,
            on_conflict="satellite_id,source_id,source_event_id,sample_index",
        )
        self.collector.upsert(
            "transactions",
            transactions,
            on_conflict="satellite_id,source_id,transaction_id",
        )
        self.collector.upsert(
            "chargers",
            chargers,
            on_conflict="satellite_id,source_id,charger_id",
        )
        success_at = _now()
        self.collector.update_satellite(
            self.satellite_id,
            {
                "current_source_id": source_id,
                "last_cursor": int(cursor["next"]),
                "last_upload_at": success_at,
            },
        )
        state = ForwarderState(
            source_id=source_id,
            cursor=int(cursor["next"]),
            last_success_at=success_at,
            last_error=None,
        )
        self.state_store.save(state)
        return state, bool(cursor["more"])

    def run(
        self,
        *,
        poll_seconds: float,
        max_backoff_seconds: float = 60.0,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        backoff = 1.0
        while True:
            try:
                _, more = self.forward_once()
                backoff = 1.0
                if not more:
                    sleeper(poll_seconds)
            except Exception as exc:
                LOGGER.error("OCPP Forwarder retrying after failure: %s", exc)
                current = self.state_store.load()
                self.state_store.save(replace(current, last_error=str(exc)))
                sleeper(backoff)
                backoff = min(max_backoff_seconds, max(backoff * 2, 1.0))
