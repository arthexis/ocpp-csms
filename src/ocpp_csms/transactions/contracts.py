from __future__ import annotations

from typing import Any, Iterable

from ocpp_csms.output import json_command_result
from ocpp_csms.transactions.query import TransactionView

TRANSACTIONS_SCHEMA = "ocpp-csms/transactions/v1"


def _int_or_none(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _payload(record: dict[str, Any], name: str) -> dict[str, Any] | None:
    value = record.get(name)
    return value if isinstance(value, dict) else None


def transaction_item(view: TransactionView, *, local_time: bool = False) -> dict[str, Any]:
    """Serialize one transaction into the stable machine-readable contract."""
    record = view.record
    start = _payload(record, "start")
    stop = _payload(record, "stop")

    meter_start = _int_or_none(start.get("meter_start")) if start else None
    meter_stop = _int_or_none(stop.get("meter_stop")) if stop else None
    energy_wh = None
    if meter_start is not None and meter_stop is not None:
        energy_wh = meter_stop - meter_start

    started_at = record.get("start_received_at") if local_time else (start.get("timestamp") if start else None)
    stopped_at = record.get("stop_received_at") if local_time else (stop.get("timestamp") if stop else None)
    if local_time:
        started_at = started_at or record.get("created_at")
        stopped_at = stopped_at or (record.get("updated_at") if stop else None)

    return {
        "id": view.transaction_id,
        "charger_id": view.charge_point_id,
        "connector_id": view.connector_id,
        "rfid": view.id_tag,
        "state": view.status,
        "active": view.active,
        "started_at": started_at,
        "stopped_at": stopped_at,
        "meter_start_wh": meter_start,
        "meter_stop_wh": meter_stop,
        "energy_wh": energy_wh,
        "last_activity_at": view.event_time(local_time=local_time),
    }


def transactions_contract(views: Iterable[TransactionView], *, local_time: bool = False) -> dict[str, Any]:
    return json_command_result(
        {"transactions": [transaction_item(view, local_time=local_time) for view in views]},
        schema=TRANSACTIONS_SCHEMA,
    )
